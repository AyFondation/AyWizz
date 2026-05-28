# =============================================================================
# File: test_v1_contract_pin.py
# Version: 1
# Path: ay_platform_core/tests/integration/_smoke/test_v1_contract_pin.py
# Description: Trivial smoke tests pinning the contract of v1 endpoints
#              that are otherwise covered only by the auth-matrix:
#
#              - HEALTH endpoints (C6, C7) — return 200 + a known
#                shape, no auth.
#              - 501 STUB endpoints (C5 versions, C7 refresh) —
#                return 501 with a non-empty `detail` so the contract
#                is observable to clients ("not implemented" rather
#                than silent silence).
#
#              These are not feature tests — they pin the API surface
#              so an accidental "fixed the stub by returning 200" or
#              "removed health" can't ship without the test going red.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import NullPublisher as C5NullPublisher
from ay_platform_core.c5_requirements.router import router as c5_router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.db.repository import ValidationRepository
from ay_platform_core.c6_validation.plugin.registry import get_registry as c6_get_registry
from ay_platform_core.c6_validation.router import router as c6_router
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import ValidationSnapshotStorage
from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


_HEADERS = {
    "X-User-Id": "u-smoke",
    "X-Tenant-Id": "tenant-smoke",
    "X-User-Roles": "project_editor",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-smoke",
    )


# ---------------------------------------------------------------------------
# Component apps — minimal wiring; reused by every smoke test in the file.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def c6_app(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> Iterator[FastAPI]:
    db_name = f"c6_smoke_{uuid.uuid4().hex[:8]}"
    bucket = f"c6-smoke-{uuid.uuid4().hex[:6]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = ValidationRepository(db)
    repo._ensure_collections_sync()
    minio = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    snapshot_store = ValidationSnapshotStorage(minio, bucket)
    snapshot_store._ensure_bucket_sync()
    service = ValidationService(
        config=ValidationConfig(),
        registry=c6_get_registry(),
        repo=repo,
        snapshot_store=snapshot_store,
    )
    app = FastAPI()
    app.include_router(c6_router)
    app.state.validation_service = service
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)
        cleanup_minio_bucket(minio_container, bucket)


@pytest.fixture(scope="function")
def c7_app(arango_container: ArangoEndpoint) -> Iterator[FastAPI]:
    db_name = f"c7_smoke_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
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
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture(scope="function")
def c5_app(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> Iterator[FastAPI]:
    db_name = f"c5_smoke_{uuid.uuid4().hex[:8]}"
    bucket = f"c5-smoke-{uuid.uuid4().hex[:6]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = RequirementsRepository(db)
    repo._ensure_collections_sync()
    minio = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = RequirementsStorage(minio, bucket)
    storage._ensure_bucket_sync()
    service = RequirementsService(repo, storage, C5NullPublisher())
    app = FastAPI()
    app.include_router(c5_router)
    app.state.requirements_service = service
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)
        cleanup_minio_bucket(minio_container, bucket)


# ---------------------------------------------------------------------------
# Health endpoints — open, no auth, status: ok
# ---------------------------------------------------------------------------


async def test_c6_validation_health_returns_ok(c6_app: FastAPI) -> None:
    async with _client(c6_app) as c:
        response = await c.get("/api/v1/validation/health")
    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok"


async def test_c7_memory_health_returns_ok(c7_app: FastAPI) -> None:
    async with _client(c7_app) as c:
        response = await c.get("/api/v1/memory/health")
    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok"


# ---------------------------------------------------------------------------
# 501 stub endpoints — pin the "not implemented" contract
# ---------------------------------------------------------------------------


async def test_c5_entity_versions_returns_501_stub(c5_app: FastAPI) -> None:
    """Point-in-time export deferred to v2 (R-300-080..083). The
    endpoint MUST exist and return 501 with a non-empty detail so
    clients can detect the gap programmatically."""
    async with _client(c5_app) as c:
        response = await c.get(
            "/api/v1/projects/p-smoke/requirements/entities/E-100-001/versions/3",
            headers={"X-User-Id": "u-smoke"},
        )
    assert response.status_code == 501
    assert response.json().get("detail")


async def test_c7_memory_refresh_status_returns_501_stub(c7_app: FastAPI) -> None:
    """Refresh job deferred (R-400-060/061). Stub returns 501 with a
    descriptive message."""
    async with _client(c7_app) as c:
        response = await c.get(
            "/api/v1/memory/refresh/job-smoke",
            headers={"X-User-Id": "u-smoke", "X-Tenant-Id": "tenant-smoke"},
        )
    assert response.status_code == 501
    detail = response.json().get("detail", "")
    assert "deferred" in detail or "refresh" in detail.lower()


# Touch unused imports to keep file lint-clean.
_ = (_HEADERS,)
