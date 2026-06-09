# =============================================================================
# File: test_source_diagnostics.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_source_diagnostics.py
# Description: Unit tests for the source observability surface —
#              `get_source_diagnostics` (index status + MinIO storage paths +
#              per-chunk status) and the MinIO delete cascade (R-100-082).
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import ParseStatus
from ay_platform_core.c7_memory.service import MemoryService


class _FakeRepo:
    def __init__(self, *, source: dict[str, Any] | None, chunks: list[dict[str, Any]]) -> None:
        self._source = source
        self._chunks = chunks
        self.deleted_chunks = False
        self.deleted_source = False

    async def get_source(self, t: str, p: str, s: str) -> dict[str, Any] | None:
        return self._source

    async def list_chunks_for_source(self, t: str, p: str, s: str) -> list[dict[str, Any]]:
        return self._chunks

    async def source_enrichment_cost(
        self, t: str, p: str, s: str
    ) -> dict[str, Any] | None:
        return None  # no C8 cost collection in the unit fake

    async def delete_chunks_for_source(self, t: str, p: str, s: str) -> int:
        self.deleted_chunks = True
        return len(self._chunks)

    async def delete_source(self, t: str, p: str, s: str) -> None:
        self.deleted_source = True


class _FakeStorage:
    def __init__(self) -> None:
        self.prefix_deletes: list[tuple[str, str]] = []
        self.blob_deletes: list[dict[str, Any]] = []

    async def delete_prefix(self, *, bucket: str, prefix: str) -> int:
        self.prefix_deletes.append((bucket, prefix))
        return 1

    async def delete_source_blob(self, **kwargs: Any) -> None:
        self.blob_deletes.append(kwargs)


def _make_service(repo: _FakeRepo, storage: _FakeStorage | None = None) -> MemoryService:
    return MemoryService(
        config=MemoryConfig(c13_artifacts_bucket="c13-extractor-artifacts"),
        repo=repo,  # type: ignore[arg-type]
        embedder=DeterministicHashEmbedder(dimension=128),
        storage=storage,  # type: ignore[arg-type]
    )


def _source_row() -> dict[str, Any]:
    return {
        "source_id": "src-1",
        "project_id": "p1",
        "mime_type": "application/pdf",
        "size_bytes": 2048,
        "uploaded_by": "alice",
        "uploaded_at": "2026-06-01T00:00:00+00:00",
        "parse_status": ParseStatus.INDEXED.value,
        "parse_error": None,
        "chunk_count": 2,
        "model_id": "all-minilm",
        "processing_version": "chunk=512/64;embed=all-minilm",
        "minio_raw_path": "sources/t1/p1/src-1/raw.pdf",
    }


def _chunk_row(
    chunk_id: str, seq: int, run_id: str, *, vector: list[float] | None
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "chunk_index": seq,
        "vector": vector,
        "metadata": {
            "extraction_run_id": run_id,
            "token_count": 12,
            "char_start": seq * 100,
            "char_end": seq * 100 + 80,
        },
    }


@pytest.mark.asyncio
async def test_diagnostics_surfaces_status_storage_and_chunks() -> None:
    repo = _FakeRepo(
        source=_source_row(),
        chunks=[
            _chunk_row("c1", 0, "run-xyz", vector=[0.1] * 128),
            _chunk_row("c2", 1, "run-xyz", vector=[0.2] * 128),
        ],
    )
    service = _make_service(repo)

    diag = await service.get_source_diagnostics("t1", "p1", "src-1")

    assert diag.parse_status == ParseStatus.INDEXED
    assert diag.chunk_count == 2
    assert diag.extraction_run_id == "run-xyz"
    # MinIO locations.
    assert diag.storage.raw_object_key == "sources/t1/p1/src-1/raw.pdf"
    assert diag.storage.artifacts_prefix == "t1/p1/src-1/runs/run-xyz/"
    assert diag.storage.chunks_jsonl_key == "t1/p1/src-1/runs/run-xyz/02_chunks/chunks.jsonl"
    # Per-chunk status.
    assert [c.chunk_id for c in diag.chunks] == ["c1", "c2"]
    assert all(c.has_embedding for c in diag.chunks)
    assert diag.chunks[1].char_start == 100


@pytest.mark.asyncio
async def test_diagnostics_pending_source_has_no_run_or_chunks() -> None:
    row = _source_row()
    row["parse_status"] = ParseStatus.PENDING.value
    row["chunk_count"] = 0
    repo = _FakeRepo(source=row, chunks=[])
    service = _make_service(repo)

    diag = await service.get_source_diagnostics("t1", "p1", "src-1")
    assert diag.parse_status == ParseStatus.PENDING
    assert diag.extraction_run_id is None
    assert diag.storage.artifacts_prefix is None
    # The raw location is known even before extraction completes.
    assert diag.storage.raw_object_key == "sources/t1/p1/src-1/raw.pdf"
    assert diag.chunks == []


@pytest.mark.asyncio
async def test_diagnostics_missing_source_404() -> None:
    service = _make_service(_FakeRepo(source=None, chunks=[]))
    with pytest.raises(HTTPException) as exc:
        await service.get_source_diagnostics("t1", "p1", "nope")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_cascades_to_db_and_minio() -> None:
    """R-100-082 — delete removes the chunks + source row AND the MinIO raw
    blob + C13 run artifacts."""
    repo = _FakeRepo(source=_source_row(), chunks=[_chunk_row("c1", 0, "run-xyz", vector=[0.1])])
    storage = _FakeStorage()
    service = _make_service(repo, storage)

    await service.delete_source("t1", "p1", "src-1")

    # DB cascade.
    assert repo.deleted_chunks is True
    assert repo.deleted_source is True
    # MinIO cascade: the memory-bucket download blob + the C13 raw + artifacts.
    assert len(storage.blob_deletes) == 1
    deleted_prefixes = {p for _, p in storage.prefix_deletes}
    assert "sources/t1/p1/src-1/" in deleted_prefixes  # raw in C13 bucket
    assert "t1/p1/src-1/runs/" in deleted_prefixes  # C13 run artifacts
