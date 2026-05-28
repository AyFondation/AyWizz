# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/integration/c6_validation/conftest.py
# Description: Fixtures for C6 integration tests. Real ArangoDB + MinIO via
#              testcontainers, C6 service wired in-process with the built-in
#              `code` plugin.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

# Importing the C6 package triggers the built-in plugin's self-registration
# (R-700-002 — build-time plugin discovery). Without this, `get_registry()`
# returns no plugins and the service rejects every trigger with 404.
import ay_platform_core.c6_validation  # noqa: F401 — side-effect import
from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.db.repository import ValidationRepository
from ay_platform_core.c6_validation.plugin.registry import get_registry
from ay_platform_core.c6_validation.router import router
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)


@pytest.fixture(scope="function")
def c6_repo(arango_container: ArangoEndpoint) -> Iterator[ValidationRepository]:
    db_name = f"c6_test_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db(
        "_system", username="root", password=arango_container.password
    )
    sys_db.create_database(db_name)
    try:
        db = client.db(
            db_name, username="root", password=arango_container.password
        )
        repo = ValidationRepository(db)
        repo._ensure_collections_sync()
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture(scope="function")
def c6_snapshot_store(
    minio_container: MinioEndpoint,
) -> Iterator[ValidationSnapshotStorage]:
    bucket = f"c6-test-{uuid.uuid4().hex[:8]}"
    client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    store = ValidationSnapshotStorage(client, bucket)
    store._ensure_bucket_sync()
    try:
        yield store
    finally:
        cleanup_minio_bucket(minio_container, bucket)


@pytest.fixture(scope="function")
def c6_config() -> ValidationConfig:
    return ValidationConfig(max_findings_per_run=100)


@pytest.fixture(scope="function")
def c6_service(
    c6_config: ValidationConfig,
    c6_repo: ValidationRepository,
    c6_snapshot_store: ValidationSnapshotStorage,
) -> ValidationService:
    return ValidationService(
        config=c6_config,
        registry=get_registry(),
        repo=c6_repo,
        snapshot_store=c6_snapshot_store,
    )


@pytest.fixture(scope="function")
def c6_app(c6_service: ValidationService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.validation_service = c6_service
    return app
