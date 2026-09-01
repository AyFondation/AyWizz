# =============================================================================
# File: test_live_docs.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_live_docs.py
# Description: Unit tests for the light live-docs ingestion path (D-021 /
#              R-400-230/232) over a fake repo: chunk+embed under LIVE_DOCS,
#              type dispatch (prose/code/tabular), re-index REPLACES in place,
#              remove clears, and re-embed coverage of LIVE_DOCS chunks on an
#              embedding-model change.
#
# @relation validates:R-400-230
# @relation validates:R-400-232
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = pytest.mark.unit

_TENANT = "t1"
_PROJECT = "p1"


class _FakeRepo:
    def __init__(self) -> None:
        self.chunks: list[dict[str, Any]] = []

    async def delete_chunks_for_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> int:
        before = len(self.chunks)
        self.chunks = [c for c in self.chunks if c.get("source_id") != source_id]
        return before - len(self.chunks)

    async def upsert_chunks(self, rows: list[dict[str, Any]]) -> None:
        keys = {r["_key"] for r in rows}
        self.chunks = [c for c in self.chunks if c["_key"] not in keys]
        self.chunks.extend(dict(r) for r in rows)

    async def list_active_chunks_for_index(
        self, tenant_id: str, project_id: str, index: str
    ) -> list[dict[str, Any]]:
        return [
            c for c in self.chunks
            if c.get("index") == index and c.get("status") == "active"
        ]

    async def list_sources(
        self, tenant_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        return []

    async def list_active_entity_chunks_for_project(
        self, tenant_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        return []


def _service(embedder: DeterministicHashEmbedder) -> tuple[MemoryService, _FakeRepo]:
    repo = _FakeRepo()
    config = MemoryConfig(chunk_token_size=64, chunk_overlap=8)
    svc = MemoryService(config=config, repo=repo, embedder=embedder)  # type: ignore[arg-type]
    return svc, repo


_PROSE = "The frobulator widget streams thimble data into the knowledge graph."


async def test_ingest_prose_creates_livedoc_chunks() -> None:
    svc, repo = _service(DeterministicHashEmbedder(model_id="m1", dimension=64))
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="docs/notes.md", content=_PROSE
    )
    assert res.doc_type == "prose"
    assert res.chunk_count >= 1
    assert res.model_id == "m1"
    assert res.reduced_fidelity is False
    assert all(c["index"] == "live_docs" for c in repo.chunks)
    assert all(c["metadata"]["live_doc_path"] == "docs/notes.md" for c in repo.chunks)
    assert all(len(c["vector"]) == 64 for c in repo.chunks)


async def test_ingest_code_dispatches_code_type() -> None:
    svc, _ = _service(DeterministicHashEmbedder(model_id="m1", dimension=64))
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py", content="def f():\n    return 1\n"
    )
    assert res.doc_type == "code"


async def test_ingest_tabular_falls_back_prose_reduced_fidelity() -> None:
    svc, _ = _service(DeterministicHashEmbedder(model_id="m1", dimension=64))
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="data/sheet.xlsx", content="a,b\n1,2\n"
    )
    assert res.doc_type == "tabular"
    assert res.reduced_fidelity is True
    assert res.chunk_count >= 1  # still indexed (prose fallback), not dropped


async def test_reindex_same_path_replaces_in_place() -> None:
    svc, repo = _service(DeterministicHashEmbedder(model_id="m1", dimension=64))
    await svc.ingest_live_document(_TENANT, _PROJECT, path="a.md", content=_PROSE)
    n1 = len(repo.chunks)
    # Re-index the SAME path with new content → old chunks replaced, not doubled.
    await svc.ingest_live_document(
        _TENANT, _PROJECT, path="a.md", content="completely different content here"
    )
    paths = {c["metadata"]["live_doc_path"] for c in repo.chunks}
    assert paths == {"a.md"}
    assert len(repo.chunks) >= 1
    # A DIFFERENT path adds, does not replace.
    await svc.ingest_live_document(_TENANT, _PROJECT, path="b.md", content=_PROSE)
    assert {c["metadata"]["live_doc_path"] for c in repo.chunks} == {"a.md", "b.md"}
    assert len(repo.chunks) > n1


async def test_remove_live_document_clears_its_chunks() -> None:
    svc, repo = _service(DeterministicHashEmbedder(model_id="m1", dimension=64))
    await svc.ingest_live_document(_TENANT, _PROJECT, path="a.md", content=_PROSE)
    await svc.ingest_live_document(_TENANT, _PROJECT, path="b.md", content=_PROSE)
    removed = await svc.remove_live_document(_TENANT, _PROJECT, path="a.md")
    assert removed >= 1
    assert {c["metadata"]["live_doc_path"] for c in repo.chunks} == {"b.md"}


async def test_reembed_covers_live_docs_on_model_change() -> None:
    svc, repo = _service(DeterministicHashEmbedder(model_id="m1", dimension=64))
    await svc.ingest_live_document(_TENANT, _PROJECT, path="a.md", content=_PROSE)
    assert all(c["model_id"] == "m1" for c in repo.chunks)
    # Operator switches the project's embedding model.
    svc._embedder = DeterministicHashEmbedder(model_id="m2", dimension=32)
    result = await svc.reembed_project(_TENANT, _PROJECT)
    units = {s.source_id: s for s in result.sources}
    assert "<project-live-docs>" in units
    assert units["<project-live-docs>"].status == "reembedded"
    assert all(c["model_id"] == "m2" and len(c["vector"]) == 32 for c in repo.chunks)
