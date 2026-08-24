# =============================================================================
# File: test_reembed.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_reembed.py
# Description: Unit tests for MemoryService.reembed_project (D-011 / R-400-222).
#              Validates re-embed-ONLY semantics: vectors + model provenance are
#              recomputed from the stored chunk text (content + context), the
#              source row's model_id/processing_version are advanced, and the
#              skip/failed classifications (already-current model, not indexed,
#              no active chunks, adapter failure) behave per contract. Uses a
#              fake repo so no container is needed.
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.models import ChunkStatus, ParseStatus
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = pytest.mark.unit

_TENANT = "t1"
_PROJECT = "proj-A"
_NEW_MODEL = "new-model"
_OLD_MODEL = "old-model"


class _FakeRepo:
    def __init__(
        self,
        sources: list[dict[str, Any]],
        chunks: dict[str, list[dict[str, Any]]],
        entity_chunks: list[dict[str, Any]] | None = None,
    ) -> None:
        self._sources = sources
        self._chunks = chunks
        self._entity_chunks = entity_chunks or []
        self.upserted_chunks: list[dict[str, Any]] = []
        self.upserted_sources: list[dict[str, Any]] = []

    async def list_sources(
        self, tenant_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        return self._sources

    async def list_chunks_for_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> list[dict[str, Any]]:
        return self._chunks.get(source_id, [])

    async def list_active_entity_chunks_for_project(
        self, tenant_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        return self._entity_chunks

    async def upsert_chunks(self, rows: list[dict[str, Any]]) -> None:
        self.upserted_chunks.extend(rows)

    async def upsert_source(self, row: dict[str, Any]) -> None:
        self.upserted_sources.append(row)


def _source(source_id: str, *, model_id: str, indexed: bool = True) -> dict[str, Any]:
    return {
        "_key": f"{_TENANT}:{_PROJECT}:{source_id}",
        "source_id": source_id,
        "tenant_id": _TENANT,
        "project_id": _PROJECT,
        "parse_status": (ParseStatus.INDEXED if indexed else ParseStatus.PENDING).value,
        "model_id": model_id,
        "chunk_count": 1,
        "processing_version": f"chunk=512/64;embed={model_id}",
    }


def _chunk(
    source_id: str, idx: int, *, model_id: str, status: ChunkStatus = ChunkStatus.ACTIVE
) -> dict[str, Any]:
    chunk_id = f"{source_id}:{idx}"
    return {
        "_key": f"{_TENANT}:{_PROJECT}:{chunk_id}",
        "chunk_id": chunk_id,
        "tenant_id": _TENANT,
        "project_id": _PROJECT,
        "index": "external_sources",
        "source_id": source_id,
        "chunk_index": idx,
        "content": f"chunk {idx} of {source_id}",
        "context": "doc context",
        "content_hash": "sha256:deadbeef",
        "vector": [0.0] * 128,
        "model_id": model_id,
        "model_dim": 128,
        "status": status.value,
    }


def _service(repo: _FakeRepo) -> MemoryService:
    embedder = DeterministicHashEmbedder(model_id=_NEW_MODEL, dimension=128)
    config = MemoryConfig(
        chunk_token_size=512,
        chunk_overlap=64,
    )
    # No resolver + no storage: _embedder_for returns the global embedder, and
    # the artifact refresh is skipped (storage is optional).
    return MemoryService(config=config, repo=repo, embedder=embedder)  # type: ignore[arg-type]


async def test_reembeds_stale_source_and_advances_provenance() -> None:
    repo = _FakeRepo(
        sources=[_source("s1", model_id=_OLD_MODEL)],
        chunks={"s1": [_chunk("s1", 0, model_id=_OLD_MODEL)]},
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)

    assert (result.reembedded, result.skipped, result.failed) == (1, 0, 0)
    assert result.model_id == _NEW_MODEL
    # The chunk was rewritten onto the new model, with a real (non-zero) vector.
    assert len(repo.upserted_chunks) == 1
    new_chunk = repo.upserted_chunks[0]
    assert new_chunk["model_id"] == _NEW_MODEL
    assert new_chunk["model_dim"] == 128
    assert sum(abs(v) for v in new_chunk["vector"]) > 0
    # Text/identity are preserved — this is re-embed only.
    assert new_chunk["content"] == "chunk 0 of s1"
    assert new_chunk["_key"] == f"{_TENANT}:{_PROJECT}:s1:0"
    # The source row advanced to the new model + processing version.
    assert repo.upserted_sources[0]["model_id"] == _NEW_MODEL
    assert repo.upserted_sources[0]["processing_version"] == f"chunk=512/64;embed={_NEW_MODEL}"


async def test_source_already_on_current_model_is_skipped() -> None:
    repo = _FakeRepo(
        sources=[_source("s1", model_id=_NEW_MODEL)],
        chunks={"s1": [_chunk("s1", 0, model_id=_NEW_MODEL)]},
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    assert (result.reembedded, result.skipped, result.failed) == (0, 1, 0)
    assert repo.upserted_chunks == []
    assert repo.upserted_sources == []


async def test_non_indexed_source_is_skipped() -> None:
    repo = _FakeRepo(
        sources=[_source("s1", model_id=_OLD_MODEL, indexed=False)],
        chunks={"s1": [_chunk("s1", 0, model_id=_OLD_MODEL)]},
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    assert (result.reembedded, result.skipped, result.failed) == (0, 1, 0)
    assert repo.upserted_chunks == []


async def test_source_without_active_chunks_is_skipped() -> None:
    repo = _FakeRepo(
        sources=[_source("s1", model_id=_OLD_MODEL)],
        chunks={
            "s1": [_chunk("s1", 0, model_id=_OLD_MODEL, status=ChunkStatus.SUPERSEDED)]
        },
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    assert (result.reembedded, result.skipped, result.failed) == (0, 1, 0)
    assert result.sources[0].detail == "no active chunks"


async def test_adapter_failure_is_isolated_per_source() -> None:
    class _BoomEmbedder(DeterministicHashEmbedder):
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("ollama unreachable")

    repo = _FakeRepo(
        sources=[_source("s1", model_id=_OLD_MODEL)],
        chunks={"s1": [_chunk("s1", 0, model_id=_OLD_MODEL)]},
    )
    svc = _service(repo)
    svc._embedder = _BoomEmbedder(model_id=_NEW_MODEL, dimension=128)
    result = await svc.reembed_project(_TENANT, _PROJECT)
    assert (result.reembedded, result.skipped, result.failed) == (0, 0, 1)
    assert "ollama unreachable" in (result.sources[0].detail or "")
    # The old index is left intact — nothing was written.
    assert repo.upserted_chunks == []
    assert repo.upserted_sources == []


async def test_mixed_batch_aggregates_counts() -> None:
    repo = _FakeRepo(
        sources=[
            _source("stale", model_id=_OLD_MODEL),
            _source("current", model_id=_NEW_MODEL),
            _source("pending", model_id=_OLD_MODEL, indexed=False),
        ],
        chunks={
            "stale": [_chunk("stale", 0, model_id=_OLD_MODEL)],
            "current": [_chunk("current", 0, model_id=_NEW_MODEL)],
            "pending": [_chunk("pending", 0, model_id=_OLD_MODEL)],
        },
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    assert result.total_sources == 3
    assert (result.reembedded, result.skipped, result.failed) == (1, 2, 0)


def _entity_chunk(idx: int, *, model_id: str) -> dict[str, Any]:
    """A source-less entity/requirements chunk (embed_entity: source_id=null,
    no `context` field)."""
    chunk_id = f"E-{idx}@v1"
    return {
        "_key": f"{_TENANT}:{_PROJECT}:{chunk_id}",
        "chunk_id": chunk_id,
        "tenant_id": _TENANT,
        "project_id": _PROJECT,
        "index": "requirements",
        "source_id": None,
        "entity_id": f"E-{idx}",
        "entity_version": 1,
        "chunk_index": 0,
        "content": f"entity {idx} text",
        "content_hash": "sha256:deadbeef",
        "vector": [0.0] * 128,
        "model_id": model_id,
        "model_dim": 128,
        "status": ChunkStatus.ACTIVE.value,
    }


async def test_entity_chunks_are_reembedded() -> None:
    # No sources at all — only source-less entity embeddings.
    repo = _FakeRepo(
        sources=[],
        chunks={},
        entity_chunks=[_entity_chunk(0, model_id=_OLD_MODEL)],
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    assert (result.reembedded, result.skipped, result.failed) == (1, 0, 0)
    assert result.total_sources == 1
    assert result.sources[0].source_id == "<project-entities>"
    assert len(repo.upserted_chunks) == 1
    assert repo.upserted_chunks[0]["model_id"] == _NEW_MODEL
    # Entity chunks have no source row → none written.
    assert repo.upserted_sources == []


async def test_entity_chunks_already_current_skipped() -> None:
    repo = _FakeRepo(
        sources=[],
        chunks={},
        entity_chunks=[_entity_chunk(0, model_id=_NEW_MODEL)],
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    assert (result.reembedded, result.skipped, result.failed) == (0, 1, 0)
    assert repo.upserted_chunks == []


async def test_no_entities_adds_nothing_to_report() -> None:
    repo = _FakeRepo(
        sources=[_source("s1", model_id=_OLD_MODEL)],
        chunks={"s1": [_chunk("s1", 0, model_id=_OLD_MODEL)]},
        entity_chunks=[],
    )
    result = await _service(repo).reembed_project(_TENANT, _PROJECT)
    # Only the one source unit — no synthetic entity unit when none exist.
    assert result.total_sources == 1
    assert all(o.source_id != "<project-entities>" for o in result.sources)
