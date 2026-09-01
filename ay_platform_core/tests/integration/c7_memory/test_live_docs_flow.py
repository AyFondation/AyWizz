# =============================================================================
# File: test_live_docs_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_live_docs_flow.py
# Description: Integration tests for the light live-docs -> RAG path (D-021 /
#              R-400-230/232) against a REAL ArangoDB, exercising the full
#              endpoint chains: PUT /live-docs/index -> retrieve finds it ->
#              re-index REPLACES -> DELETE removes (retrieve empty) -> reembed
#              covers live-docs on a model change -> role gate (viewer 403).
#
# @relation validates:R-400-230
# @relation validates:R-400-232
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.models import IndexKind
from ay_platform_core.c7_memory.router import router
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = pytest.mark.integration

_TENANT = "tenant-a"
_PROJECT = "p1"
_OWNER = {"X-User-Id": "alice", "X-Tenant-Id": _TENANT, "X-User-Roles": "project_owner"}
_EDITOR = {"X-User-Id": "eve", "X-Tenant-Id": _TENANT, "X-User-Roles": "project_editor"}
_VIEWER = {"X-User-Id": "bob", "X-Tenant-Id": _TENANT, "X-User-Roles": "project_viewer"}

_DOC = (
    "The frobulator widget streams thimble data into the project knowledge "
    "graph and emits structured retrieval events for downstream agents."
)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _make_service(repo: MemoryRepository, model_id: str = "model-v1") -> MemoryService:
    return MemoryService(
        config=MemoryConfig(
            chunk_token_size=32, chunk_overlap=4,
            default_quota_bytes=1024 * 1024, retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=DeterministicHashEmbedder(model_id=model_id, dimension=64),
    )


def _app(service: MemoryService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.memory_service = service
    return app


async def _retrieve(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    r = await client.post(
        "/api/v1/memory/retrieve", headers=_OWNER,
        json={"project_id": _PROJECT, "query": query,
              "indexes": [IndexKind.LIVE_DOCS.value], "top_k": 5},
    )
    r.raise_for_status()
    hits: list[dict[str, Any]] = r.json()["hits"]
    return hits


@pytest.mark.asyncio
async def test_live_doc_index_retrieve_reindex_remove(
    c7_repo: MemoryRepository,
) -> None:
    app = _app(_make_service(c7_repo))
    async with _client(app) as c:
        # 1. Index an authored live-doc (as an editor).
        r = await c.put(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/index",
            headers=_EDITOR, json={"path": "docs/spec.md", "content": _DOC},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["doc_type"] == "prose"
        assert body["chunk_count"] >= 1
        assert body["reduced_fidelity"] is False

        # 2. Retrieval over LIVE_DOCS finds it.
        hits = await _retrieve(c, "frobulator widget data")
        assert hits, "live-doc not retrievable after index"

        # 3. Re-index the SAME path with new content → replaces (no duplication).
        r = await c.put(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/index",
            headers=_EDITOR,
            json={"path": "docs/spec.md",
                  "content": "A rewritten note about quantum widgets only."},
        )
        assert r.status_code == 200
        hits2 = await _retrieve(c, "quantum widgets")
        assert hits2, "re-indexed content not retrievable"
        # Old content should no longer dominate — all live-doc chunks are the
        # single current doc.
        rows = await c7_repo.list_active_chunks_for_index(
            _TENANT, _PROJECT, IndexKind.LIVE_DOCS.value
        )
        assert {r["metadata"]["live_doc_path"] for r in rows} == {"docs/spec.md"}

        # 4. Delete the live-doc → its chunks are gone.
        path_seg = "docs/spec.md"
        r = await c.delete(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/index/{path_seg}",
            headers=_EDITOR,
        )
        assert r.status_code == 204, r.text
        after = await c7_repo.list_active_chunks_for_index(
            _TENANT, _PROJECT, IndexKind.LIVE_DOCS.value
        )
        assert after == []


@pytest.mark.asyncio
async def test_live_doc_reembed_on_model_change(c7_repo: MemoryRepository) -> None:
    service = _make_service(c7_repo, model_id="model-v1")
    app = _app(service)
    async with _client(app) as c:
        r = await c.put(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/index",
            headers=_OWNER, json={"path": "a.md", "content": _DOC},
        )
        assert r.status_code == 200
    before = await c7_repo.list_active_chunks_for_index(
        _TENANT, _PROJECT, IndexKind.LIVE_DOCS.value
    )
    assert before and all(c["model_id"] == "model-v1" for c in before)

    # Operator switches the project's embedding model → reembed covers LIVE_DOCS.
    service._embedder = DeterministicHashEmbedder(model_id="model-v2", dimension=32)
    async with _client(app) as c:
        r = await c.post(
            f"/api/v1/memory/projects/{_PROJECT}/reembed", headers=_OWNER
        )
        assert r.status_code == 200, r.text
        units = {s["source_id"] for s in r.json()["sources"]}
        assert "<project-live-docs>" in units

    after = await c7_repo.list_active_chunks_for_index(
        _TENANT, _PROJECT, IndexKind.LIVE_DOCS.value
    )
    assert after and all(
        c["model_id"] == "model-v2" and len(c["vector"]) == 32 for c in after
    )


@pytest.mark.asyncio
async def test_live_doc_index_role_gate(c7_repo: MemoryRepository) -> None:
    app = _app(_make_service(c7_repo))
    async with _client(app) as c:
        r = await c.put(
            f"/api/v1/memory/projects/{_PROJECT}/live-docs/index",
            headers=_VIEWER, json={"path": "x.md", "content": _DOC},
        )
    assert r.status_code == 403, r.text
