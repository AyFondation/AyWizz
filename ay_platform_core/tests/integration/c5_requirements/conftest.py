# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/conftest.py
# Description: Fixtures for C5 integration tests — isolated MinIO bucket +
#              ArangoDB database per test function, wrapped FastAPI app.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import NullPublisher
from ay_platform_core.c5_requirements.router import router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)


@pytest.fixture(scope="function")
def c5_repo(arango_container: ArangoEndpoint) -> Iterator[RequirementsRepository]:
    """Isolated requirements repository on a fresh ArangoDB database."""
    db_name = f"c5_test_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = RequirementsRepository(db)
        repo._ensure_collections_sync()
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture(scope="function")
def c5_storage(minio_container: MinioEndpoint) -> Iterator[RequirementsStorage]:
    """Isolated requirements storage on a fresh bucket."""
    bucket = f"c5-test-{uuid.uuid4().hex[:8]}"
    client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = RequirementsStorage(client, bucket)
    storage._ensure_bucket_sync()
    try:
        yield storage
    finally:
        cleanup_minio_bucket(minio_container, bucket)


@pytest.fixture(scope="function")
def c5_publisher() -> NullPublisher:
    return NullPublisher()


@pytest.fixture(scope="function")
def c5_service(
    c5_repo: RequirementsRepository,
    c5_storage: RequirementsStorage,
    c5_publisher: NullPublisher,
) -> RequirementsService:
    return RequirementsService(c5_repo, c5_storage, c5_publisher)


@pytest.fixture(scope="function")
def c5_app(c5_service: RequirementsService) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.requirements_service = c5_service
    return app
