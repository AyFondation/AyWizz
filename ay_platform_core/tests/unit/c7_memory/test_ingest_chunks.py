# =============================================================================
# File: test_ingest_chunks.py
# Version: 3
# Path: ay_platform_core/tests/unit/c7_memory/test_ingest_chunks.py
# Description: Unit tests for the C7 endpoint `ingest_chunks_from_extractor`
#              — R-400-223 **v3**: the request carries only the RUN
#              REFERENCE; C7 reads `run_manifest.json` + `chunks.jsonl`
#              from the C13 artifacts bucket itself (C12/n8n no longer
#              marshals object bytes — R-100-081 v3). Uses an in-memory
#              fake repo + a fake storage seeding the C13 artifacts.
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import (
    ChunkIngestRequest,
    ChunkRich,
    ParseStatus,
)
from ay_platform_core.c7_memory.service import MemoryService

RUN_ID = "20260528_1200_abcdef"


class _FakeRepo:
    """In-memory fake repo — captures upserts for assertions."""

    def __init__(self) -> None:
        self.chunks: list[dict[str, Any]] = []
        self.sources: dict[str, dict[str, Any]] = {}
        self.quota_used = 0

    async def upsert_chunks(self, rows: list[dict[str, Any]]) -> None:
        self.chunks.extend(rows)

    async def upsert_source(self, row: dict[str, Any]) -> None:
        self.sources[row["_key"]] = row

    async def quota_totals(self, tenant_id: str, project_id: str) -> dict[str, int]:
        return {"bytes_used": self.quota_used, "source_count": len(self.sources)}


class _FakeStorage:
    """Fake C7 storage seeding the C13 run artifacts. Returns the manifest
    / chunks.jsonl bytes by key suffix (the service derives the real keys
    via `MemorySourceStorage.c13_artifact_key`)."""

    def __init__(self, *, manifest: dict[str, Any], chunks: list[ChunkRich]) -> None:
        self._manifest = manifest
        self._chunks = chunks
        self.put_artifact_calls: list[dict[str, Any]] = []

    async def get_extraction_artifact(self, *, bucket: str, key: str) -> bytes:
        if key.endswith("run_manifest.json"):
            return json.dumps(self._manifest).encode("utf-8")
        if key.endswith("chunks.jsonl"):
            lines = [json.dumps(c.model_dump()) for c in self._chunks]
            return ("\n".join(lines)).encode("utf-8")
        raise FileNotFoundError(f"{bucket}/{key}")

    async def put_artifact(self, **kwargs: Any) -> None:
        self.put_artifact_calls.append(kwargs)


def _chunk(
    chunk_id: str,
    seq: int,
    text: str,
    embedding: list[float] | None = None,
    *,
    token_count: int | None = None,
) -> ChunkRich:
    return ChunkRich(
        chunk_id=chunk_id,
        seq=seq,
        text=text,
        original_text=text,
        section_path=[],
        char_start=0,
        char_end=len(text),
        token_count=token_count if token_count is not None else len(text.split()),
        extraction_run_id=RUN_ID,
        embedding=embedding,
    )


def _make_service(
    repo: _FakeRepo, storage: _FakeStorage
) -> MemoryService:
    embedder = DeterministicHashEmbedder(dimension=128)
    config = MemoryConfig(
        chunk_token_size=512,
        chunk_overlap=64,
        default_quota_bytes=10 * 1024 * 1024,
    )
    return MemoryService(
        config=config,
        repo=repo,  # type: ignore[arg-type]
        embedder=embedder,
        storage=storage,  # type: ignore[arg-type]
    )


def _payload() -> ChunkIngestRequest:
    return ChunkIngestRequest(
        extraction_run_id=RUN_ID,
        uploaded_by="user-alice",
        mime_type="application/pdf",
        tenant_id="t1",
    )


@pytest.mark.asyncio
async def test_ingest_chunks_reads_artifacts_and_inserts() -> None:
    """C7 reads chunks.jsonl + manifest from MinIO and persists the vectors
    as-is (pure INSERT, no re-embedding) — R-400-223 v3."""
    repo = _FakeRepo()
    vector = [0.1] * 128
    storage = _FakeStorage(
        manifest={"embedding_model": "voyage-3", "embedding_dimension": 128},
        chunks=[
            _chunk("c:0001", 0, "Hello world chunk one.", embedding=vector),
            _chunk("c:0002", 1, "Second chunk content.", embedding=vector),
        ],
    )
    service = _make_service(repo, storage)

    result = await service.ingest_chunks_from_extractor(
        tenant_id="t1", project_id="p1", source_id="src-1", payload=_payload(),
    )

    assert result.chunk_count == 2
    assert result.parse_status == ParseStatus.INDEXED
    assert len(repo.chunks) == 2
    for row in repo.chunks:
        assert row["vector"] == vector  # taken from the artifact, not re-embedded
        assert row["model_id"] == "voyage-3"
        assert row["model_dim"] == 128
    source = next(iter(repo.sources.values()))
    assert "embed=voyage-3" in source["processing_version"]


@pytest.mark.asyncio
async def test_ingest_chunks_missing_embedding_422() -> None:
    """v3 has no embed-fallback: a chunk without a vector is a 422 (the
    embeddings are produced by C13, not C7)."""
    repo = _FakeRepo()
    storage = _FakeStorage(
        manifest={"embedding_model": "voyage-3", "embedding_dimension": 128},
        chunks=[_chunk("c:0001", 0, "No vector here.", embedding=None)],
    )
    service = _make_service(repo, storage)

    with pytest.raises(HTTPException) as exc_info:
        await service.ingest_chunks_from_extractor(
            tenant_id="t1", project_id="p1", source_id="src-2", payload=_payload(),
        )
    assert exc_info.value.status_code == 422
    assert "embedding" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ingest_chunks_dimension_mismatch_400() -> None:
    """A vector whose length disagrees with the manifest dimension → 400."""
    repo = _FakeRepo()
    storage = _FakeStorage(
        manifest={"embedding_model": "voyage-3", "embedding_dimension": 128},
        chunks=[_chunk("c:0001", 0, "Mismatched dims.", embedding=[0.1] * 64)],
    )
    service = _make_service(repo, storage)

    with pytest.raises(HTTPException) as exc_info:
        await service.ingest_chunks_from_extractor(
            tenant_id="t1", project_id="p1", source_id="src-3", payload=_payload(),
        )
    assert exc_info.value.status_code == 400
    assert "embedding_dimension" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ingest_chunks_artifacts_missing_404() -> None:
    """When the run artifacts are absent in MinIO, C7 surfaces a 404."""
    repo = _FakeRepo()

    class _EmptyStorage:
        async def get_extraction_artifact(self, *, bucket: str, key: str) -> bytes:
            raise FileNotFoundError(f"{bucket}/{key}")

        async def put_artifact(self, **kwargs: Any) -> None:
            return None

    service = _make_service(repo, _EmptyStorage())  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await service.ingest_chunks_from_extractor(
            tenant_id="t1", project_id="p1", source_id="src-4", payload=_payload(),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_ingest_chunks_persists_rich_metadata() -> None:
    """Section path, char offsets, references, images, tables, and
    extraction_run_id SHALL be preserved in row metadata. `search_text` is a
    TOP-LEVEL field (so the ArangoSearch view indexes it). `global_summary` is
    NOT duplicated per chunk — it is stored ONCE on the source row."""
    repo = _FakeRepo()
    chunk = ChunkRich(
        chunk_id="c:0042",
        seq=42,
        text="Chunk with rich metadata.",
        search_text="Chapter 2 > 2.3 Architecture\n\nChunk with rich metadata.",
        original_text="Chunk with rich metadata.",
        context_summary="Cumulative summary so far.",
        global_summary="Document-level dense summary.",
        section_path=["Chapter 2", "2.3 Architecture"],
        char_start=1024,
        char_end=1049,
        token_count=4,
        references=["ref:smith-2023"],
        images=["img_abc12345"],
        tables=["tbl_001"],
        extraction_run_id="20260528_1200_xyz",
        embedding=[0.2] * 128,
    )
    storage = _FakeStorage(
        manifest={"embedding_model": "voyage-3", "embedding_dimension": 128},
        chunks=[chunk],
    )
    service = _make_service(repo, storage)

    await service.ingest_chunks_from_extractor(
        tenant_id="t1", project_id="p1", source_id="src-5", payload=_payload(),
    )

    row = repo.chunks[0]
    meta = row["metadata"]
    assert meta["section_path"] == ["Chapter 2", "2.3 Architecture"]
    assert meta["char_start"] == 1024
    assert meta["extraction_run_id"] == "20260528_1200_xyz"
    assert meta["images"] == ["img_abc12345"]
    # `search_text` is top-level (BM25-indexed), = exactly what C13 embedded.
    assert row["search_text"] == "Chapter 2 > 2.3 Architecture\n\nChunk with rich metadata."
    # NO per-chunk duplication of the document summary …
    assert "global_summary" not in meta
    # … it lives ONCE on the source row instead.
    assert repo.sources["t1:p1:src-5"]["document_summary"] == "Document-level dense summary."


@pytest.mark.asyncio
async def test_ingest_chunks_enforces_quota() -> None:
    """Cumulative token_count → byte estimate against the project quota
    (R-400-024)."""
    repo = _FakeRepo()
    storage = _FakeStorage(
        manifest={"embedding_model": "voyage-3", "embedding_dimension": 128},
        chunks=[_chunk("c:0001", 0, "x " * 100, embedding=[0.1] * 128, token_count=1000)],
    )
    service = _make_service(repo, storage)
    repo.quota_used = service._config.default_quota_bytes  # already at cap

    with pytest.raises(HTTPException) as exc_info:
        await service.ingest_chunks_from_extractor(
            tenant_id="t1", project_id="p1", source_id="src-6", payload=_payload(),
        )
    assert exc_info.value.status_code in (413, 403)
