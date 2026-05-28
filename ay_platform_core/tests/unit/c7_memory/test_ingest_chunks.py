# =============================================================================
# File: test_ingest_chunks.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_ingest_chunks.py
# Description: Unit tests for the D-020 session 5 C7 endpoint
#              `ingest_chunks_from_extractor` — R-400-223 v2 pure-INSERT
#              path. Uses an in-memory fake `MemoryRepository` + the
#              `DeterministicHashEmbedder` already shipped in c7_memory
#              (for the backward-compat fallback path).
# =============================================================================

from __future__ import annotations

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


class _FakeRepo:
    """In-memory fake repo for unit tests — captures upserts so the test
    asserts on the rows the service persisted."""

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


@pytest.fixture
def fake_repo() -> _FakeRepo:
    return _FakeRepo()


@pytest.fixture
def embedder() -> DeterministicHashEmbedder:
    return DeterministicHashEmbedder(dimension=128)


@pytest.fixture
def service(fake_repo: _FakeRepo, embedder: DeterministicHashEmbedder) -> MemoryService:
    config = MemoryConfig(
        embedding_adapter="deterministic-hash",
        embedding_model_id=embedder.model_id,
        embedding_dimension=embedder.dimension,
        chunk_token_size=512,
        chunk_overlap=64,
        default_quota_bytes=10 * 1024 * 1024,
    )
    # _FakeRepo is structurally compatible with the methods MemoryService
    # uses on the unit-test surface (upsert_chunks / upsert_source /
    # project_storage_bytes). The full MemoryRepository protocol is
    # exercised by the integration tier.
    return MemoryService(config=config, repo=fake_repo, embedder=embedder)  # type: ignore[arg-type]


def _chunk(
    chunk_id: str,
    seq: int,
    text: str,
    embedding: list[float] | None = None,
) -> ChunkRich:
    return ChunkRich(
        chunk_id=chunk_id,
        seq=seq,
        text=text,
        original_text=text,
        context_summary=None,
        global_summary=None,
        section_path=[],
        char_start=0,
        char_end=len(text),
        token_count=len(text.split()),
        references=[],
        images=[],
        tables=[],
        extraction_run_id="20260528_1200_abcdef",
        embedding=embedding,
    )


def _request(
    chunks: list[ChunkRich],
    embedding_model: str = "voyage-3",
    embedding_dimension: int = 128,
) -> ChunkIngestRequest:
    return ChunkIngestRequest(
        extraction_run_id="20260528_1200_abcdef",
        manifest_object_key="bucket/tenant/project/source/runs/r/00_metadata/run_manifest.json",
        embedding_model=embedding_model,
        embedding_model_version="2024-01-15",
        embedding_dimension=embedding_dimension,
        chunks=chunks,
        uploaded_by="user-alice",
        mime_type="application/pdf",
    )


@pytest.mark.asyncio
async def test_ingest_chunks_with_embeddings_is_pure_insert(
    service: MemoryService, fake_repo: _FakeRepo,
) -> None:
    """When ChunkRich.embedding is populated, C7 SHALL NOT invoke its
    own embedder — the vectors are persisted as-is per R-400-223 v2."""
    vector = [0.1] * 128
    chunks = [
        _chunk("c:0001", 0, "Hello world chunk one.", embedding=vector),
        _chunk("c:0002", 1, "Second chunk content.", embedding=vector),
    ]
    payload = _request(chunks)

    result = await service.ingest_chunks_from_extractor(
        tenant_id="t1",
        project_id="p1",
        source_id="src-1",
        payload=payload,
    )

    assert result.chunk_count == 2
    assert result.parse_status == ParseStatus.INDEXED
    assert len(fake_repo.chunks) == 2
    # Persisted vectors equal the supplied ones — no embedder invocation.
    for row in fake_repo.chunks:
        assert row["vector"] == vector
        assert row["model_id"] == "voyage-3"
        assert row["model_dim"] == 128
    # processing_version stamps the C13 embedding model (not the local one).
    source = next(iter(fake_repo.sources.values()))
    assert "embed=voyage-3" in source["processing_version"]


@pytest.mark.asyncio
async def test_ingest_chunks_missing_embedding_falls_back_to_local_embedder(
    service: MemoryService, fake_repo: _FakeRepo,
) -> None:
    """Backward-compat — when a chunk omits its embedding, C7 falls back
    to its own embedder (transitional, removed v2 per D-020 session 7)."""
    chunks = [_chunk("c:0001", 0, "Fallback embedding text.", embedding=None)]
    payload = _request(chunks, embedding_model="voyage-3", embedding_dimension=128)

    result = await service.ingest_chunks_from_extractor(
        tenant_id="t1", project_id="p1", source_id="src-2", payload=payload,
    )

    assert result.chunk_count == 1
    row = fake_repo.chunks[0]
    # The local embedder produced a non-zero deterministic vector.
    assert len(row["vector"]) == 128
    assert sum(abs(v) for v in row["vector"]) > 0


@pytest.mark.asyncio
async def test_ingest_chunks_dimension_mismatch_400(
    service: MemoryService,
) -> None:
    """A vector whose length disagrees with `embedding_dimension` SHALL
    raise HTTP 400 — defence against partial uploads."""
    chunks = [
        _chunk("c:0001", 0, "Mismatched dims.", embedding=[0.1] * 64),  # not 128
    ]
    payload = _request(chunks, embedding_dimension=128)

    with pytest.raises(HTTPException) as exc_info:
        await service.ingest_chunks_from_extractor(
            tenant_id="t1", project_id="p1", source_id="src-3", payload=payload,
        )
    assert exc_info.value.status_code == 400
    assert "embedding_dimension" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_ingest_chunks_persists_rich_metadata(
    service: MemoryService, fake_repo: _FakeRepo,
) -> None:
    """Section path, char offsets, references, images, tables, and
    extraction_run_id SHALL all be preserved in row metadata."""
    chunk = ChunkRich(
        chunk_id="c:0042",
        seq=42,
        text="Chunk with rich metadata.",
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
    payload = _request([chunk])

    await service.ingest_chunks_from_extractor(
        tenant_id="t1", project_id="p1", source_id="src-4", payload=payload,
    )

    row = fake_repo.chunks[0]
    meta = row["metadata"]
    assert meta["section_path"] == ["Chapter 2", "2.3 Architecture"]
    assert meta["char_start"] == 1024
    assert meta["extraction_run_id"] == "20260528_1200_xyz"
    assert meta["global_summary"] == "Document-level dense summary."
    assert meta["images"] == ["img_abc12345"]


@pytest.mark.asyncio
async def test_ingest_chunks_enforces_quota(
    service: MemoryService, fake_repo: _FakeRepo,
) -> None:
    """Cumulative token_count is converted to a byte estimate against the
    project quota (R-400-024)."""
    fake_repo.quota_used = service._config.default_quota_bytes  # already at cap
    chunks = [_chunk("c:0001", 0, "x " * 100, embedding=[0.1] * 128)]
    payload = _request(chunks)

    # Token count > 0 → adds to quota → cap exceeded.
    chunks[0] = ChunkRich(**{
        **chunks[0].model_dump(),
        "token_count": 1000,
    })
    payload = _request(chunks)

    with pytest.raises(HTTPException) as exc_info:
        await service.ingest_chunks_from_extractor(
            tenant_id="t1", project_id="p1", source_id="src-5", payload=payload,
        )
    # 413 from quota helper (R-400-024).
    assert exc_info.value.status_code in (413, 403)
