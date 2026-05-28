# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/integration/c9_mcp/conftest.py
# Description: Fixtures for C9 integration tests. Real C5 + C6 services wired
#              in-process (against real ArangoDB + MinIO testcontainers). The
#              MCP server is stateless so we stand it up in-process too.
#              This exercises the genuine round-trip path — no mocking at the
#              service boundary.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

import ay_platform_core.c6_validation  # noqa: F401 — side effect: register plugin
from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import NullPublisher
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.db.repository import ValidationRepository
from ay_platform_core.c6_validation.plugin.registry import get_registry
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)
from ay_platform_core.c9_mcp.config import MCPConfig
from ay_platform_core.c9_mcp.router import router as c9_router
from ay_platform_core.c9_mcp.server import MCPServer
from ay_platform_core.c9_mcp.tools.base import build_default_toolset
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

# ---------------------------------------------------------------------------
# C5 — isolated per-test repo + bucket
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def c9_c5_service(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint
) -> Iterator[RequirementsService]:
    db_name = f"c9_c5_{uuid.uuid4().hex[:8]}"
    bucket = f"c9-c5-{uuid.uuid4().hex[:8]}"
    arango_client = ArangoClient(hosts=arango_container.url)
    sys_db = arango_client.db(
        "_system", username="root", password=arango_container.password
    )
    sys_db.create_database(db_name)
    minio_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    try:
        db = arango_client.db(
            db_name, username="root", password=arango_container.password
        )
        repo = RequirementsRepository(db)
        repo._ensure_collections_sync()
        storage = RequirementsStorage(minio_client, bucket)
        storage._ensure_bucket_sync()
        yield RequirementsService(repo, storage, NullPublisher())
    finally:
        cleanup_minio_bucket(minio_container, bucket)
        cleanup_arango_database(arango_container, db_name)


# ---------------------------------------------------------------------------
# C6 — isolated per-test repo + bucket
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def c9_c6_service(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint
) -> Iterator[ValidationService]:
    db_name = f"c9_c6_{uuid.uuid4().hex[:8]}"
    bucket = f"c9-c6-{uuid.uuid4().hex[:8]}"
    arango_client = ArangoClient(hosts=arango_container.url)
    sys_db = arango_client.db(
        "_system", username="root", password=arango_container.password
    )
    sys_db.create_database(db_name)
    minio_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    try:
        db = arango_client.db(
            db_name, username="root", password=arango_container.password
        )
        repo = ValidationRepository(db)
        repo._ensure_collections_sync()
        snap = ValidationSnapshotStorage(minio_client, bucket)
        snap._ensure_bucket_sync()
        yield ValidationService(
            config=ValidationConfig(),
            registry=get_registry(),
            repo=repo,
            snapshot_store=snap,
        )
    finally:
        cleanup_minio_bucket(minio_container, bucket)
        cleanup_arango_database(arango_container, db_name)


# ---------------------------------------------------------------------------
# MCP server + FastAPI app
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def c9_server(
    c9_c5_service: RequirementsService,
    c9_c6_service: ValidationService,
) -> MCPServer:
    tools = build_default_toolset(
        c5_service=c9_c5_service, c6_service=c9_c6_service
    )
    return MCPServer(MCPConfig(), tools)


@pytest.fixture(scope="function")
def c9_app(c9_server: MCPServer) -> FastAPI:
    app = FastAPI()
    app.include_router(c9_router)
    app.state.mcp_server = c9_server
    return app
