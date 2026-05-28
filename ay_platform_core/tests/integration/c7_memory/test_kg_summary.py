# =============================================================================
# File: test_kg_summary.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_kg_summary.py
# Description: Integration test for the simple graph bootstrap —
#              GET /projects/{id}/kg/summary (R-400-200/201). Persists a
#              small KG with provenance, then asserts the summary surfaces
#              entity/relation counts + a sample of triples carrying their
#              provenance. Includes an HTTP-level smoke through the router.
#
# @relation validates:R-400-201
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.models import KGEntity, KGRelation, Provenance
from ay_platform_core.c7_memory.router import router
from ay_platform_core.c7_memory.service import MemoryService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

_TENANT = "t-kgsum"
_PROJECT = "p-kgsum"


@pytest_asyncio.fixture(scope="function")
async def kg_summary_app(
    arango_container: ArangoEndpoint,
    c7_deterministic_embedder: DeterministicHashEmbedder,
) -> AsyncIterator[tuple[FastAPI, MemoryService, KGRepository]]:
    db_name = f"c7_kgsum_{uuid.uuid4().hex[:8]}"
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
            embedding_adapter="deterministic-hash",
            embedding_model_id="deterministic-hash-v1",
            embedding_dimension=c7_deterministic_embedder.dimension,
            chunk_token_size=64,
            chunk_overlap=8,
            default_quota_bytes=1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=c7_deterministic_embedder,
        kg_repo=kg_repo,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.memory_service = service
    try:
        yield app, service, kg_repo
    finally:
        cleanup_arango_database(arango_container, db_name)


async def _seed(kg_repo: KGRepository) -> None:
    marie = KGEntity(name="Marie Curie", type="person")
    polonium = KGEntity(name="Polonium", type="concept")
    await kg_repo.persist(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id="s-kgsum",
        entities=[marie, polonium],
        relations=[KGRelation(subject=marie, relation="discovered", object=polonium)],
    )


async def test_kg_summary_counts_and_provenance(
    kg_summary_app: tuple[FastAPI, MemoryService, KGRepository],
) -> None:
    _app, service, kg_repo = kg_summary_app
    await _seed(kg_repo)

    summary = await service.kg_summary(_TENANT, _PROJECT)

    assert summary.project_id == _PROJECT
    assert summary.entity_count == 2
    assert summary.relation_count == 1
    assert len(summary.sample) == 1
    triple = summary.sample[0]
    assert triple.subject == "Marie Curie"
    assert triple.relation == "discovered"
    assert triple.object == "Polonium"
    # Provenance survives into the graph + the summary (R-400-201).
    assert triple.provenance is Provenance.INFERRED
    assert 0.0 <= triple.confidence <= 1.0


async def test_kg_summary_route_http(
    kg_summary_app: tuple[FastAPI, MemoryService, KGRepository],
) -> None:
    app, _service, kg_repo = kg_summary_app
    await _seed(kg_repo)
    headers = {
        "X-User-Id": "u1",
        "X-Tenant-Id": _TENANT,
        "X-User-Roles": "project_viewer",
    }
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/memory/projects/{_PROJECT}/kg/summary", headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_count"] == 2
    assert body["relation_count"] == 1
    assert body["sample"][0]["relation"] == "discovered"
