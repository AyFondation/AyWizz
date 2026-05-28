# =============================================================================
# File: test_structural_extraction.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_structural_extraction.py
# Description: V2 #3-A.a integration tests — DETERMINISTIC schema-guided L1
#              structural extraction (R-400-200/201, E-400-006). Wires a real
#              ArangoDB (no LLM — the extractor is deterministic), then:
#                1. ingests a requirements-spec source via the pipeline ;
#                2. POST /sources/{sid}/extract-structural ;
#                3. asserts L1 entities/edges land in memory_kg_entities /
#                   memory_kg_relations with the closed ontology types,
#                   layer=L1, provenance=extracted, confidence 1.0.
#              Covers idempotency, out-of-ontology skipping, 404, and the
#              503 when KGRepository is not wired.
#
# @relation validates:R-400-200
# @relation validates:R-400-201
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
from minio import Minio

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.models import ParseStatus, SourceIngestRequest
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

# Two requirement entities, each deriving from the same decision. Plain text
# (the deterministic extractor scans tokens, robust to whitespace collapsing).
_SPEC_SOURCE = (
    "#### R-400-200\n"
    "```yaml\n"
    "id: R-400-200\n"
    "version: 2\n"
    "derives-from: [D-016]\n"
    "```\n"
    "The structural extractor SHALL use a closed ontology.\n\n"
    "#### R-400-205\n"
    "```yaml\n"
    "id: R-400-205\n"
    "version: 1\n"
    "derives-from: [D-016]\n"
    "```\n"
    "The graph SHALL be organisable into layers.\n"
)

_HEADERS = {
    "X-User-Id": "u-struct",
    "X-Tenant-Id": "tenant-struct",
    "X-User-Roles": "project_editor",
}


@pytest_asyncio.fixture(scope="function")
async def structural_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_struct_{uuid.uuid4().hex[:8]}"
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

    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=embedder.dimension,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=embedder,
        kg_repo=kg_repo,
        # No llm_client — structural extraction is deterministic.
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service
    try:
        yield {"app": app, "service": service, "kg_repo": kg_repo}
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://e2e-struct",
    )


async def _ingest(service: MemoryService, *, source_id: str, project_id: str,
                  tenant_id: str, content: str) -> None:
    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=project_id,
            mime_type="text/plain",
            content=content,
            size_bytes=len(content.encode("utf-8")),
            uploaded_by="alice",
        ),
        tenant_id=tenant_id,
    )


async def test_extract_structural_persists_l1_graph(
    structural_stack: dict[str, Any],
) -> None:
    app: FastAPI = structural_stack["app"]
    service: MemoryService = structural_stack["service"]
    kg_repo: KGRepository = structural_stack["kg_repo"]
    source_id = f"src-spec-{uuid.uuid4().hex[:6]}"
    project_id, tenant_id = "project-struct", "tenant-struct"

    await _ingest(
        service, source_id=source_id, project_id=project_id,
        tenant_id=tenant_id, content=_SPEC_SOURCE,
    )

    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}"
            "/extract-structural",
            headers=_HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entities_added"] == 3  # R-400-200, R-400-205, D-016
    assert body["relations_added"] == 2
    assert {e["name"]: e["type"] for e in body["entities"]} == {
        "R-400-200": "REQUIREMENT",
        "R-400-205": "REQUIREMENT",
        "D-016": "DECISION",
    }

    ents = await kg_repo.list_entities_for_source(tenant_id, project_id, source_id)
    by_name = {e["name"]: e for e in ents}
    assert by_name["R-400-200"]["type"] == "REQUIREMENT"
    assert by_name["D-016"]["type"] == "DECISION"
    # Forward-compat + provenance columns (R-400-201, R-400-205).
    assert all(e["layer"] == "L1" for e in ents)
    assert all(e["provenance"] == "extracted" for e in ents)
    assert all(e["confidence"] == 1.0 for e in ents)
    assert all(e["ontology_version"] == 1 for e in ents)

    rels = await kg_repo.list_relations_for_source(tenant_id, project_id, source_id)
    assert len(rels) == 2
    assert {r["relation"] for r in rels} == {"DERIVES_FROM"}
    assert all(r["layer"] == "L1" for r in rels)


async def test_extract_structural_is_idempotent(
    structural_stack: dict[str, Any],
) -> None:
    app: FastAPI = structural_stack["app"]
    service: MemoryService = structural_stack["service"]
    kg_repo: KGRepository = structural_stack["kg_repo"]
    source_id = f"src-idem-{uuid.uuid4().hex[:6]}"
    project_id, tenant_id = "project-struct", "tenant-struct"

    await _ingest(
        service, source_id=source_id, project_id=project_id,
        tenant_id=tenant_id, content=_SPEC_SOURCE,
    )
    async with _client(app) as c:
        first = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}"
            "/extract-structural",
            headers=_HEADERS,
        )
        second = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}"
            "/extract-structural",
            headers=_HEADERS,
        )
    assert first.status_code == 200
    assert second.status_code == 200
    # The re-run adds nothing — composite keys are stable.
    assert second.json()["entities_added"] == 0
    assert second.json()["relations_added"] == 0
    ents = await kg_repo.list_entities_for_source(tenant_id, project_id, source_id)
    rels = await kg_repo.list_relations_for_source(tenant_id, project_id, source_id)
    assert len(ents) == 3
    assert len(rels) == 2


async def test_extract_structural_skips_out_of_ontology_ids(
    structural_stack: dict[str, Any],
) -> None:
    app: FastAPI = structural_stack["app"]
    service: MemoryService = structural_stack["service"]
    source_id = f"src-eskip-{uuid.uuid4().hex[:6]}"
    project_id, tenant_id = "project-struct", "tenant-struct"
    content = (
        "```yaml\nid: E-400-007\nversion: 1\n```\n"
        "```yaml\nid: R-1\nderives-from: [E-400-007]\n```\n"
    )
    await _ingest(
        service, source_id=source_id, project_id=project_id,
        tenant_id=tenant_id, content=content,
    )
    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}"
            "/extract-structural",
            headers=_HEADERS,
        )
    body = response.json()
    # E-400-007 has no ontology slot → skipped ; only R-1 (REQUIREMENT) lands,
    # and the edge to the skipped target is dropped (not coerced).
    assert {e["name"] for e in body["entities"]} == {"R-1"}
    assert body["relations_added"] == 0


async def test_extract_structural_404_for_unknown_source(
    structural_stack: dict[str, Any],
) -> None:
    app: FastAPI = structural_stack["app"]
    async with _client(app) as c:
        response = await c.post(
            "/api/v1/memory/projects/project-struct/sources/nope/extract-structural",
            headers=_HEADERS,
        )
    assert response.status_code == 404


async def test_extract_structural_503_when_kg_repo_not_wired(
    arango_container: ArangoEndpoint,
) -> None:
    db_name = f"c7_struct_no_kg_{uuid.uuid4().hex[:8]}"
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
            # No kg_repo.
        )
        app = FastAPI()
        app.include_router(c7_router)
        app.dependency_overrides[c7_get_service] = lambda: service
        async with _client(app) as c:
            response = await c.post(
                "/api/v1/memory/projects/p/sources/sid/extract-structural",
                headers=_HEADERS,
            )
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
    finally:
        cleanup_arango_database(arango_container, db_name)


# ---------------------------------------------------------------------------
# kind=code — Python AST extraction (reads raw bytes, needs MinIO storage)
# ---------------------------------------------------------------------------

_CODE_SOURCE = (
    "# @relation implements:R-400-200\n"
    "import os\n"
    "from a.b import c\n"
    "\n"
    "class Widget(Base):\n"
    "    def render(self):\n"
    "        return os.getcwd()\n"
    "\n"
    "def helper():\n"
    "    return 1\n"
)


@pytest_asyncio.fixture(scope="function")
async def code_stack(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_code_{uuid.uuid4().hex[:8]}"
    bucket = f"c7-code-{uuid.uuid4().hex[:6]}"
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
    minio = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = MemorySourceStorage(minio, bucket)
    storage._ensure_bucket_sync()
    embedder = DeterministicHashEmbedder(dimension=64)
    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=embedder.dimension,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=embedder,
        kg_repo=kg_repo,
        storage=storage,
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service
    try:
        yield {"app": app, "service": service, "kg_repo": kg_repo}
    finally:
        cleanup_arango_database(arango_container, db_name)
        cleanup_minio_bucket(minio_container, bucket)


async def test_extract_structural_code_persists_ast_graph(
    code_stack: dict[str, Any],
) -> None:
    app: FastAPI = code_stack["app"]
    service: MemoryService = code_stack["service"]
    source_id = f"pkg-mod-{uuid.uuid4().hex[:6]}"
    project_id, tenant_id = "project-struct", "tenant-struct"

    # D-020 session 7 — `ingest_uploaded_source` removed. The structural
    # extractor reads raw bytes directly from MinIO via the storage
    # adapter ; we put the source blob + a minimal C7 row in place by
    # hand (mirrors what the n8n workflow + C13 + /ingest-chunks chain
    # would land, scoped to the bare minimum this test needs).
    assert service._storage is not None, "test stack must wire `_storage`"
    await service._storage.put_source_blob(
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=source_id,
        data=_CODE_SOURCE.encode("utf-8"),
        mime_type="text/plain",
    )
    await service._repo.upsert_source({
        "_key": f"{tenant_id}:{project_id}:{source_id}",
        "tenant_id": tenant_id,
        "project_id": project_id,
        "source_id": source_id,
        "minio_raw_path": None,
        "minio_parsed_path": None,
        "minio_chunks_path": None,
        "mime_type": "text/plain",
        "size_bytes": len(_CODE_SOURCE),
        "uploaded_by": "alice",
        "uploaded_at": "2026-05-28T00:00:00+00:00",
        "parse_status": ParseStatus.INDEXED.value,
        "parse_error": None,
        "chunk_count": 0,
        "model_id": "deterministic-hash-v1",
        "processing_version": "chunk=512/64;embed=deterministic-hash-v1",
    })

    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}"
            "/extract-structural?kind=code",
            headers=_HEADERS,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    by_name = {e["name"]: e["type"] for e in body["entities"]}
    assert by_name[source_id] == "MODULE"
    assert by_name[f"{source_id}.Widget"] == "CLASS"
    assert by_name[f"{source_id}.Widget.render"] == "METHOD"
    assert by_name[f"{source_id}.helper"] == "FUNCTION"
    assert by_name["os"] == "MODULE"

    edges = {(r["subject"]["name"], r["type"], r["object"]["name"])
             for r in body["relations"]}
    assert (source_id, "IMPORTS", "os") in edges
    assert (source_id, "IMPORTS", "a.b") in edges
    assert (source_id, "DEFINES", f"{source_id}.Widget") in edges
    assert (f"{source_id}.Widget", "INHERITS_FROM", "Base") in edges
    assert (f"{source_id}.Widget", "DEFINES", f"{source_id}.Widget.render") in edges
    assert (source_id, "IMPLEMENTS", "R-400-200") in edges


async def test_extract_structural_code_409_without_raw_bytes(
    code_stack: dict[str, Any],
) -> None:
    # String-ingested source (no raw blob) → kind=code can't read original.
    app: FastAPI = code_stack["app"]
    service: MemoryService = code_stack["service"]
    source_id = f"noblob-{uuid.uuid4().hex[:6]}"
    project_id, tenant_id = "project-struct", "tenant-struct"
    await _ingest(
        service, source_id=source_id, project_id=project_id,
        tenant_id=tenant_id, content="x = 1\n",
    )
    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/memory/projects/{project_id}/sources/{source_id}"
            "/extract-structural?kind=code",
            headers=_HEADERS,
        )
    assert response.status_code == 409
