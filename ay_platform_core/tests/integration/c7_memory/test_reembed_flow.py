# =============================================================================
# File: test_reembed_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_reembed_flow.py
# Description: Integration test for POST /projects/{project_id}/reembed
#              (D-011 / R-400-222) against a REAL ArangoDB. Ingests a source
#              with one embedding model, swaps the project's embedder, calls
#              the reembed endpoint, and verifies the stored chunks + source
#              row were moved onto the new model in place — re-embed ONLY, the
#              text is untouched. Also asserts the role gate (project_viewer
#              → 403).
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.models import ChunkStatus, EntityEmbedRequest
from ay_platform_core.c7_memory.router import router
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = pytest.mark.integration

_TENANT = "tenant-a"
_PROJECT = "p1"
_SOURCE = "s1"
_OWNER = {
    "X-User-Id": "alice",
    "X-Tenant-Id": _TENANT,
    "X-User-Roles": "project_owner",
}
_VIEWER = {
    "X-User-Id": "bob",
    "X-Tenant-Id": _TENANT,
    "X-User-Roles": "project_viewer",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _make_app(service: MemoryService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.memory_service = service
    return app


@pytest.mark.asyncio
async def test_reembed_moves_chunks_to_new_model_in_place(
    c7_repo: MemoryRepository,
) -> None:
    config = MemoryConfig(
        chunk_token_size=16,
        chunk_overlap=4,
        default_quota_bytes=1024 * 1024,
        retrieval_scan_cap=1000,
    )
    service = MemoryService(
        config=config,
        repo=c7_repo,
        embedder=DeterministicHashEmbedder(model_id="model-v1", dimension=64),
    )
    app = _make_app(service)

    async with _client(app) as client:
        ingest = await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/sources",
            json={
                "source_id": _SOURCE,
                "project_id": _PROJECT,
                "mime_type": "text/plain",
                "content": (
                    "The frobulator widget processes thimble data streams and "
                    "emits structured events to the project knowledge graph."
                ),
                "size_bytes": 120,
                "uploaded_by": "alice",
            },
            headers=_OWNER,
        )
    assert ingest.status_code == 201, ingest.text
    assert ingest.json()["chunk_count"] >= 1

    before = await c7_repo.list_chunks_for_source(_TENANT, _PROJECT, _SOURCE)
    assert before and all(c["model_id"] == "model-v1" for c in before)
    assert all(c["model_dim"] == 64 for c in before)
    original_text = {c["chunk_id"]: c["content"] for c in before}

    # The operator changes the project's embedding model → the resolved
    # embedder now reports a different model_id AND a different dimension
    # (a realistic model swap). The new dimension proves the vectors were
    # genuinely recomputed (the deterministic hash is dimension-dependent).
    service._embedder = DeterministicHashEmbedder(model_id="model-v2", dimension=32)

    # Role gate: a viewer cannot trigger a re-embed.
    async with _client(app) as client:
        denied = await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/reembed", headers=_VIEWER
        )
    assert denied.status_code == 403, denied.text

    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/reembed", headers=_OWNER
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == "model-v2"
    assert body["reembedded"] == 1
    assert body["failed"] == 0

    # Backend state: every active chunk is now on model-v2 at the new
    # dimension (vectors genuinely recomputed), and the text is byte-for-byte
    # preserved (re-embed only, no re-chunk).
    after = await c7_repo.list_chunks_for_source(_TENANT, _PROJECT, _SOURCE)
    active = [c for c in after if c["status"] == ChunkStatus.ACTIVE.value]
    assert active and all(c["model_id"] == "model-v2" for c in active)
    for c in active:
        assert c["content"] == original_text[c["chunk_id"]]
        assert c["model_dim"] == 32
        assert len(c["vector"]) == 32

    # The source row advanced to the new model + processing version.
    got = await c7_repo.get_source(_TENANT, _PROJECT, _SOURCE)
    assert got is not None
    assert got["model_id"] == "model-v2"
    assert got["processing_version"] == "chunk=16/4;embed=model-v2"


@pytest.mark.asyncio
async def test_reembed_is_idempotent_when_model_unchanged(
    c7_repo: MemoryRepository,
) -> None:
    config = MemoryConfig(
        chunk_token_size=16,
        chunk_overlap=4,
        default_quota_bytes=1024 * 1024,
        retrieval_scan_cap=1000,
    )
    service = MemoryService(
        config=config,
        repo=c7_repo,
        embedder=DeterministicHashEmbedder(model_id="model-v1", dimension=64),
    )
    app = _make_app(service)

    async with _client(app) as client:
        await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/sources",
            json={
                "source_id": _SOURCE,
                "project_id": _PROJECT,
                "mime_type": "text/plain",
                "content": "widget data stream events graph project memory",
                "size_bytes": 80,
                "uploaded_by": "alice",
            },
            headers=_OWNER,
        )
        # Same model still selected → nothing to do, all sources skipped.
        resp = await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/reembed", headers=_OWNER
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reembedded"] == 0
    assert body["skipped"] == 1
    assert body["sources"][0]["detail"] == "already on the current embedding model"


@pytest.mark.asyncio
async def test_reembed_covers_sourceless_entity_chunks(
    c7_repo: MemoryRepository,
) -> None:
    """D-coverage: `embed_entity` chunks (source_id=null, no source row) are
    re-embedded too — the per-source loop alone would miss them."""
    config = MemoryConfig(
        chunk_token_size=16,
        chunk_overlap=4,
        default_quota_bytes=1024 * 1024,
        retrieval_scan_cap=1000,
    )
    service = MemoryService(
        config=config,
        repo=c7_repo,
        embedder=DeterministicHashEmbedder(model_id="model-v1", dimension=64),
    )
    app = _make_app(service)

    # Embed a standalone entity (no source) on model-v1.
    await service.embed_entity(
        EntityEmbedRequest(
            project_id=_PROJECT,
            entity_id="R-001",
            entity_version=1,
            content="the system SHALL persist audit events",
        ),
        tenant_id=_TENANT,
    )
    entities = await c7_repo.list_active_entity_chunks_for_project(_TENANT, _PROJECT)
    assert entities and entities[0]["model_id"] == "model-v1"
    assert entities[0]["source_id"] is None

    # Operator switches the embedding model, then re-embeds.
    service._embedder = DeterministicHashEmbedder(model_id="model-v2", dimension=32)
    async with _client(app) as client:
        resp = await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/reembed", headers=_OWNER
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reembedded"] == 1
    units = {s["source_id"]: s for s in body["sources"]}
    assert "<project-entities>" in units

    after = await c7_repo.list_active_entity_chunks_for_project(_TENANT, _PROJECT)
    assert after and all(c["model_id"] == "model-v2" for c in after)
    assert all(c["model_dim"] == 32 and len(c["vector"]) == 32 for c in after)
