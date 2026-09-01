# =============================================================================
# File: test_live_docs_kg_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_live_docs_kg_flow.py
# Description: Integration tests for the structural KG dispatch of the light
#              live-docs path (D-021 / R-400-231/232) against a REAL ArangoDB:
#                1. a Python live-doc lands an L1 code graph (module + symbols) ;
#                2. re-index with a renamed symbol is IDEMPOTENT — the stale
#                   node is purged (proves purge-before-persist) ;
#                3. a requirements live-doc lands requirement entities ;
#                4. prose lands NO structural graph ;
#                5. remove purges the document's KG contribution.
#
# @relation validates:R-400-231
# @relation validates:R-400-232
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c7_memory.service import MemoryService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-lkg"
_PROJECT = "project-lkg"


@pytest_asyncio.fixture(scope="function")
async def stack(arango_container: ArangoEndpoint) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_lkg_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = MemoryRepository(db)
    repo._ensure_collections_sync()
    kg_repo = KGRepository(db)
    kg_repo._ensure_collections_sync()
    service = MemoryService(
        config=MemoryConfig(
            chunk_token_size=64, chunk_overlap=8,
            default_quota_bytes=1024 * 1024 * 1024, retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=DeterministicHashEmbedder(model_id="m1", dimension=64),
        kg_repo=kg_repo,
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.state.memory_service = service
    try:
        yield {"service": service, "kg_repo": kg_repo, "app": app}
    finally:
        cleanup_arango_database(arango_container, db_name)


_EDITOR = {
    "X-User-Id": "eve", "X-Tenant-Id": _TENANT, "X-User-Roles": "project_editor",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _entity_names(kg_repo: KGRepository, source_id: str) -> set[str]:
    rows = await kg_repo.list_entities_for_source(_TENANT, _PROJECT, source_id)
    return {r["name"] for r in rows}


async def test_python_livedoc_lands_code_graph_and_is_idempotent(
    stack: dict[str, Any],
) -> None:
    service: MemoryService = stack["service"]
    kg_repo: KGRepository = stack["kg_repo"]
    path = "src/mod.py"
    source_id = MemoryService._livedoc_source_id(path)

    res = await service.ingest_live_document(
        _TENANT, _PROJECT, path=path,
        content="def frobulate(x):\n    return x + 1\n",
    )
    assert res.doc_type == "code"
    assert res.kg_indexed is True
    names = await _entity_names(kg_repo, source_id)
    assert any(n.endswith("frobulate") for n in names), names

    # Re-index with the function RENAMED → purge-before-persist means the old
    # `frobulate` node must be gone, not linger alongside the new one.
    res2 = await service.ingest_live_document(
        _TENANT, _PROJECT, path=path,
        content="def thimblize(x):\n    return x + 2\n",
    )
    assert res2.kg_indexed is True
    names2 = await _entity_names(kg_repo, source_id)
    assert any(n.endswith("thimblize") for n in names2), names2
    assert not any(n.endswith("frobulate") for n in names2), names2


async def test_requirements_livedoc_lands_requirement_graph(
    stack: dict[str, Any],
) -> None:
    service: MemoryService = stack["service"]
    kg_repo: KGRepository = stack["kg_repo"]
    path = "reqs/spec.md"
    source_id = MemoryService._livedoc_source_id(path)
    content = (
        "id: R-999-001\n"
        "derives-from: D-016\n"
        "The widget SHALL frobulate the thimble stream.\n\n"
        "id: R-999-002\n"
        "derives-from: D-016\n"
        "The widget SHALL emit retrieval events.\n"
    )
    res = await service.ingest_live_document(
        _TENANT, _PROJECT, path=path, content=content
    )
    assert res.doc_type == "requirements"
    assert res.kg_indexed is True
    names = await _entity_names(kg_repo, source_id)
    assert names, "requirements live-doc produced no KG entities"


async def test_prose_livedoc_lands_no_structural_graph(
    stack: dict[str, Any],
) -> None:
    service: MemoryService = stack["service"]
    kg_repo: KGRepository = stack["kg_repo"]
    path = "docs/notes.md"
    source_id = MemoryService._livedoc_source_id(path)
    res = await service.ingest_live_document(
        _TENANT, _PROJECT, path=path,
        content="A plain prose note about widgets and thimbles, no structure.",
    )
    assert res.doc_type == "prose"
    assert res.kg_indexed is False
    assert await _entity_names(kg_repo, source_id) == set()


async def test_remove_purges_livedoc_kg(stack: dict[str, Any]) -> None:
    service: MemoryService = stack["service"]
    kg_repo: KGRepository = stack["kg_repo"]
    path = "src/app.py"
    source_id = MemoryService._livedoc_source_id(path)
    await service.ingest_live_document(
        _TENANT, _PROJECT, path=path,
        content="class Widget:\n    def run(self):\n        return 1\n",
    )
    assert await _entity_names(kg_repo, source_id), "expected KG entities pre-remove"

    await service.remove_live_document(_TENANT, _PROJECT, path=path)
    assert await _entity_names(kg_repo, source_id) == set()


async def test_kg_indexed_endpoint_reports_membership(
    stack: dict[str, Any],
) -> None:
    # HTTP read path (R-200-173 / R-400-232): GET .../live-docs/kg-indexed
    # reflects real KG membership by path.
    service: MemoryService = stack["service"]
    app: FastAPI = stack["app"]
    await service.ingest_live_document(
        _TENANT, _PROJECT, path="src/mod.py",
        content="def frobulate(x):\n    return x + 1\n",
    )
    async with _client(app) as c:
        r_code = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/kg-indexed",
            headers=_EDITOR, params={"path": "src/mod.py"},
        )
        r_prose = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/kg-indexed",
            headers=_EDITOR, params={"path": "does/not/exist.md"},
        )
    assert r_code.status_code == 200, r_code.text
    assert r_code.json() == {"path": "src/mod.py", "kg_indexed": True}
    assert r_prose.status_code == 200
    assert r_prose.json()["kg_indexed"] is False
