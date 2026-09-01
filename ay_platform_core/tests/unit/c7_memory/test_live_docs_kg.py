# =============================================================================
# File: test_live_docs_kg.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_live_docs_kg.py
# Description: Unit tests for the structural knowledge-graph dispatch of the
#              light live-docs path (D-021 / R-400-231/232) over a fake KG
#              repo: Python code and requirements documents trigger a
#              structural extraction (purge-then-persist, idempotent); prose,
#              tabular and non-Python code do NOT; the `kg_indexed` signal
#              tracks it; a KG failure is best-effort (never fails the index);
#              remove purges the document's KG contribution.
#
# @relation validates:R-400-231
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
        self.chunks.extend(dict(r) for r in rows)


class _FakeKGRepo:
    """Records the ordered (op, source_id) sequence so tests can assert
    purge-before-persist idempotency, and keeps a per-source entity store so
    `list_entities_for_source` (the membership read) is exercised too."""

    def __init__(self, *, fail_persist: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail_persist = fail_persist
        self.entities: dict[str, list[dict[str, str]]] = {}

    async def purge_source(
        self, *, tenant_id: str, project_id: str, source_id: str
    ) -> tuple[int, int]:
        self.calls.append(("purge", source_id))
        removed = self.entities.pop(source_id, [])
        return (len(removed), 0)

    async def persist_structural(
        self, *, tenant_id: str, project_id: str, source_id: str, extraction: Any
    ) -> tuple[int, int]:
        if self._fail_persist:
            raise RuntimeError("arango down")
        self.calls.append(("persist", source_id))
        self.entities[source_id] = [{"name": e.name} for e in extraction.entities]
        return (len(extraction.entities), len(extraction.relations))

    async def list_entities_for_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> list[dict[str, str]]:
        return list(self.entities.get(source_id, []))


def _service(
    kg: _FakeKGRepo | None,
) -> tuple[MemoryService, _FakeRepo, _FakeKGRepo | None]:
    repo = _FakeRepo()
    config = MemoryConfig(chunk_token_size=64, chunk_overlap=8)
    svc = MemoryService(
        config=config,
        repo=repo,  # type: ignore[arg-type]
        embedder=DeterministicHashEmbedder(model_id="m1", dimension=64),
        kg_repo=kg,  # type: ignore[arg-type]
    )
    return svc, repo, kg


_PY_CODE = "def frobulate(x):\n    return x + 1\n\nclass Widget:\n    def run(self):\n        return frobulate(1)\n"  # noqa: E501
_REQ_DOC = (
    "id: R-999-001\n"
    "derives-from: R-999-000\n"
    "The widget shall frobulate the thimble stream.\n"
)
_PROSE = "The frobulator widget streams thimble data into the knowledge graph."


async def test_python_code_triggers_structural_kg() -> None:
    svc, _, kg = _service(_FakeKGRepo())
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py", content=_PY_CODE
    )
    assert res.doc_type == "code"
    assert res.kg_indexed is True
    assert kg is not None
    ops = [c[0] for c in kg.calls]
    assert ops == ["purge", "persist"]  # idempotent: purge BEFORE persist


async def test_requirements_doc_triggers_structural_kg() -> None:
    svc, _, kg = _service(_FakeKGRepo())
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="reqs/spec.md", content=_REQ_DOC
    )
    assert res.doc_type == "requirements"
    assert res.kg_indexed is True
    assert kg is not None
    assert [c[0] for c in kg.calls] == ["purge", "persist"]


async def test_prose_does_not_trigger_kg() -> None:
    svc, _, kg = _service(_FakeKGRepo())
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="docs/notes.md", content=_PROSE
    )
    assert res.doc_type == "prose"
    assert res.kg_indexed is False
    assert kg is not None
    assert kg.calls == []


async def test_tabular_does_not_trigger_kg() -> None:
    svc, _, kg = _service(_FakeKGRepo())
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="data/sheet.xlsx", content="a,b\n1,2\n"
    )
    assert res.doc_type == "tabular"
    assert res.kg_indexed is False
    assert kg is not None
    assert kg.calls == []


async def test_non_python_code_does_not_trigger_kg() -> None:
    # The deterministic structural extractor is Python-only; a TS/Go live-doc
    # is still classified `code` and vector-indexed, but carries no L1 graph.
    svc, _, kg = _service(_FakeKGRepo())
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/app.ts",
        content="export function f(x: number) { return x + 1; }\n",
    )
    assert res.doc_type == "code"
    assert res.kg_indexed is False
    assert kg is not None
    assert kg.calls == []


async def test_kg_extraction_is_best_effort() -> None:
    # A KG/persist failure must NOT fail the light index path: chunks stand,
    # kg_indexed reports False, no exception propagates.
    svc, repo, _kg = _service(_FakeKGRepo(fail_persist=True))
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py", content=_PY_CODE
    )
    assert res.doc_type == "code"
    assert res.kg_indexed is False
    assert res.chunk_count >= 1  # vector chunks were still written
    assert len(repo.chunks) >= 1


async def test_no_kg_repo_wired_reports_not_indexed() -> None:
    svc, _, _ = _service(None)
    res = await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py", content=_PY_CODE
    )
    assert res.kg_indexed is False  # nothing wired → no graph, no crash


async def test_remove_purges_kg_contribution() -> None:
    svc, _, kg = _service(_FakeKGRepo())
    await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py", content=_PY_CODE
    )
    assert kg is not None
    kg.calls.clear()
    await svc.remove_live_document(_TENANT, _PROJECT, path="src/mod.py")
    assert len(kg.calls) == 1
    assert kg.calls[0][0] == "purge"


async def test_livedoc_kg_indexed_reflects_membership() -> None:
    # The read path (R-200-173 / R-400-232): True after a code index, False for
    # prose, and False (no crash) when no KG repo is wired.
    svc, _, _ = _service(_FakeKGRepo())
    assert (
        await svc.livedoc_kg_indexed(_TENANT, _PROJECT, path="src/mod.py")
    ) is False  # nothing indexed yet
    await svc.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py", content=_PY_CODE
    )
    assert (
        await svc.livedoc_kg_indexed(_TENANT, _PROJECT, path="src/mod.py")
    ) is True
    await svc.ingest_live_document(
        _TENANT, _PROJECT, path="docs/notes.md", content=_PROSE
    )
    assert (
        await svc.livedoc_kg_indexed(_TENANT, _PROJECT, path="docs/notes.md")
    ) is False


async def test_livedoc_kg_indexed_false_without_repo() -> None:
    svc, _, _ = _service(None)
    assert (
        await svc.livedoc_kg_indexed(_TENANT, _PROJECT, path="src/mod.py")
    ) is False
