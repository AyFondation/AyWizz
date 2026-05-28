# =============================================================================
# File: test_kg_extraction.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_kg_extraction.py
# Description: Phase F.1 integration tests — LLM-based KG extraction.
#              Wires real ArangoDB + a scripted C8 LLM that returns
#              canned JSON, then drives:
#                1. ingest a source via the existing pipeline;
#                2. POST /sources/{sid}/extract-kg;
#                3. assert entities + relations land in
#                   memory_kg_entities and memory_kg_relations.
#              Also covers the failure paths: 503 when LLM not wired,
#              502 on malformed LLM response, 404 on unknown source.
#
# @relation validates:R-400-021
# =============================================================================

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Header, HTTPException, Request

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.models import SourceIngestRequest
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


# ---------------------------------------------------------------------------
# Scripted C8 — returns canned JSON wrapped in OpenAI response shape.
# ---------------------------------------------------------------------------


class _ScriptedKGLLM:
    """Captures every call and returns a fixed JSON body (or raises a
    canned HTTP error for failure-path tests)."""

    def __init__(self, json_payload: str) -> None:
        self.json_payload = json_payload
        self.calls_seen: list[dict[str, Any]] = []
        self.next_status = 200


def _build_mock_llm_app(scripted: _ScriptedKGLLM) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(
        request: Request,
        x_agent_name: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> Any:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="bearer required")
        if not x_agent_name or not x_session_id:
            raise HTTPException(status_code=400, detail="missing tags")
        body = await request.json()
        scripted.calls_seen.append(body)
        if scripted.next_status != 200:
            raise HTTPException(status_code=scripted.next_status, detail="forced")
        return {
            "id": f"mock-{len(scripted.calls_seen)}",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": scripted.json_payload,
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def kg_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_kg_{uuid.uuid4().hex[:8]}"
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
    embedder = DeterministicHashEmbedder(dimension=64)

    scripted = _ScriptedKGLLM(json_payload=json.dumps({
        "entities": [
            {"name": "Marie Curie", "type": "person"},
            {"name": "Polonium", "type": "concept"},
            {"name": "Sorbonne", "type": "organization"},
        ],
        "relations": [
            {
                "subject": {"name": "Marie Curie", "type": "person"},
                "relation": "discovered",
                "object": {"name": "Polonium", "type": "concept"},
            },
            {
                "subject": {"name": "Marie Curie", "type": "person"},
                "relation": "taught_at",
                "object": {"name": "Sorbonne", "type": "organization"},
            },
        ],
    }))

    mock_app = _build_mock_llm_app(scripted)
    llm_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_app),
        base_url="http://mock/v1",
    )
    llm_client = LLMGatewayClient(
        ClientSettings(gateway_url="http://mock/v1"),
        bearer_token="kg-test-token",
        http_client=llm_http,
    )

    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=embedder.dimension,
            chunk_token_size=64,
            chunk_overlap=8,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=embedder,
        kg_repo=kg_repo,
        llm_client=llm_client,
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service

    try:
        yield {
            "app": app,
            "service": service,
            "kg_repo": kg_repo,
            "scripted": scripted,
            "llm_http": llm_http,
            "db": db,
        }
    finally:
        await llm_http.aclose()
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI, *, raise_exceptions: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_exceptions),
        base_url="http://e2e-kg",
    )


_HEADERS = {
    "X-User-Id": "u-kg",
    "X-Tenant-Id": "tenant-kg",
    "X-User-Roles": "project_editor",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_extract_kg_persists_entities_and_relations(
    kg_stack: dict[str, Any],
) -> None:
    """End-to-end: ingest a source, call extract-kg, assert the canned
    LLM JSON lands as 3 entities + 2 relations in the KG collections."""
    app: FastAPI = kg_stack["app"]
    service: MemoryService = kg_stack["service"]
    kg_repo: KGRepository = kg_stack["kg_repo"]

    source_id = f"src-mc-{uuid.uuid4().hex[:6]}"
    project_id = "project-kg"
    tenant_id = "tenant-kg"

    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=project_id,
            mime_type="text/plain",
            content=(
                "Marie Curie discovered Polonium and later taught at the Sorbonne."
            ),
            size_bytes=80,
            uploaded_by="alice",
        ),
        tenant_id=tenant_id,
    )

    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-kg",
            headers=_HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_id"] == source_id
    assert body["entities_added"] == 3
    assert body["relations_added"] == 2
    assert {e["name"] for e in body["entities"]} == {
        "Marie Curie", "Polonium", "Sorbonne",
    }

    # Direct repo assertions — entities + relations are persisted under
    # the right tenant/project scope.
    ents = await kg_repo.list_entities_for_source(tenant_id, project_id, source_id)
    assert {e["name"] for e in ents} == {"Marie Curie", "Polonium", "Sorbonne"}

    rels = await kg_repo.list_relations_for_source(tenant_id, project_id, source_id)
    assert len(rels) == 2
    assert {r["relation"] for r in rels} == {"discovered", "taught_at"}


async def test_extract_kg_idempotent_on_re_run(
    kg_stack: dict[str, Any],
) -> None:
    """Re-running extraction on the same source SHALL NOT duplicate
    entities or relations — composite keys are stable on
    (tenant, project, name, type) for entities and on
    (subj, relation, obj) for edges."""
    app: FastAPI = kg_stack["app"]
    service: MemoryService = kg_stack["service"]
    kg_repo: KGRepository = kg_stack["kg_repo"]
    source_id = f"src-idem-{uuid.uuid4().hex[:6]}"
    project_id = "project-kg-idem"
    tenant_id = "tenant-kg-idem"
    headers = {
        "X-User-Id": "u-kg",
        "X-Tenant-Id": tenant_id,
        "X-User-Roles": "project_editor",
    }

    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=project_id,
            mime_type="text/plain",
            content="Curie and Polonium and Sorbonne, take two.",
            size_bytes=64,
            uploaded_by="alice",
        ),
        tenant_id=tenant_id,
    )

    async with _client(app) as c:
        first = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-kg",
            headers=headers,
        )
        second = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-kg",
            headers=headers,
        )
    assert first.status_code == 200
    assert second.status_code == 200

    ents = await kg_repo.list_entities_for_source(tenant_id, project_id, source_id)
    rels = await kg_repo.list_relations_for_source(tenant_id, project_id, source_id)
    # Same entities + relations, no duplicates from the second run.
    assert len(ents) == 3
    assert len(rels) == 2


async def test_extract_kg_returns_404_for_unknown_source(
    kg_stack: dict[str, Any],
) -> None:
    app: FastAPI = kg_stack["app"]
    async with _client(app) as c:
        response = await c.post(
            "/api/v1/memory/projects/project-kg/sources/does-not-exist/extract-kg",
            headers=_HEADERS,
        )
    assert response.status_code == 404


async def test_extract_kg_returns_502_on_malformed_llm_response(
    kg_stack: dict[str, Any],
) -> None:
    """If the LLM doesn't return JSON, the endpoint surfaces a 502
    (BAD_GATEWAY) rather than indexing nothing silently."""
    app: FastAPI = kg_stack["app"]
    service: MemoryService = kg_stack["service"]
    scripted: _ScriptedKGLLM = kg_stack["scripted"]

    source_id = f"src-bad-{uuid.uuid4().hex[:6]}"
    project_id = "project-kg-bad"
    tenant_id = "tenant-kg-bad"

    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=project_id,
            mime_type="text/plain",
            content="Some content.",
            size_bytes=16,
            uploaded_by="alice",
        ),
        tenant_id=tenant_id,
    )

    scripted.json_payload = "definitely not json {{"
    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-kg",
            headers={
                "X-User-Id": "u-kg",
                "X-Tenant-Id": tenant_id,
                "X-User-Roles": "project_editor",
            },
        )
    assert response.status_code == 502
    assert "extraction failed" in response.json()["detail"]


async def test_extract_kg_returns_503_when_llm_not_wired(
    arango_container: ArangoEndpoint,
) -> None:
    """A C7 instance without an `llm_client` injection SHALL return 503
    on the extract endpoint (not crash, not silently no-op)."""
    db_name = f"c7_kg_no_llm_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    try:
        db = ArangoClient(hosts=arango_container.url).db(
            db_name, username="root", password=arango_container.password,
        )
        repo = MemoryRepository(db)
        repo._ensure_collections_sync()
        embedder = DeterministicHashEmbedder(dimension=64)
        service = MemoryService(
            config=MemoryConfig(embedding_dimension=embedder.dimension),
            repo=repo,
            embedder=embedder,
            # No kg_repo, no llm_client
        )
        app = FastAPI()
        app.include_router(c7_router)
        app.dependency_overrides[c7_get_service] = lambda: service

        # Even without a source, the 503 SHALL fire before the source
        # lookup, so no need to ingest first.
        async with _client(app) as c:
            response = await c.post(
                "/api/v1/memory/projects/p/sources/sid/extract-kg",
                headers=_HEADERS,
            )
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
    finally:
        cleanup_arango_database(arango_container, db_name)


# Touch unused imports to keep the file lint-clean.
_ = (asyncio,)
