# =============================================================================
# File: conftest.py
# Version: 2
# Path: ay_platform_core/tests/integration/c2_auth/conftest.py
# Description: Fixtures for C2 Auth integration tests.
#              Uses the session-scoped ArangoDB testcontainer fixture and
#              creates an isolated database per test function to prevent
#              state leakage between tests.
#
# NOTE: fixtures here are sync (arango_container is sync).
#       Async test functions (asyncio_mode=auto) can use sync fixtures
#       without wrapping in pytest-asyncio.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.router import router
from ay_platform_core.c2_auth.service import AuthService, get_service
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

_SECRET = "integration-test-secret-key-32ch!"


@pytest.fixture(scope="function")
def auth_repo(arango_container: ArangoEndpoint) -> Iterator[AuthRepository]:
    """Isolated AuthRepository backed by a fresh DB within the shared container."""
    db_name = f"c2_test_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)

    repo = AuthRepository.from_config(
        arango_container.url, db_name, "root", arango_container.password
    )
    repo._ensure_collections_sync()

    try:
        yield repo
    finally:
        # Retry + verify via the shared helper. Raises if all retries fail,
        # surfacing cleanup leaks rather than hiding them behind
        # contextlib.suppress.
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture(scope="function")
def auth_service_none() -> AuthService:
    """AuthService in none mode (no ArangoDB required)."""
    config = AuthConfig.model_validate({
        "auth_mode": "none",
        "jwt_secret_key": _SECRET,
        "platform_environment": "testing",
    })
    return AuthService(config, repo=None)


@pytest.fixture(scope="function")
def auth_service_local(auth_repo: AuthRepository) -> AuthService:
    """AuthService in local mode backed by an isolated ArangoDB database."""
    config = AuthConfig.model_validate({
        "auth_mode": "local",
        "jwt_secret_key": _SECRET,
        "platform_environment": "testing",
    })
    return AuthService(config, auth_repo)


@pytest.fixture(scope="function")
def none_app(auth_service_none: AuthService) -> httpx.ASGITransport:
    """FastAPI app with none-mode service wired in."""
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_service] = lambda: auth_service_none
    return app  # type: ignore[return-value]


@pytest.fixture(scope="function")
def local_app(auth_service_local: AuthService) -> httpx.ASGITransport:
    """FastAPI app with local-mode service wired in."""
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_service] = lambda: auth_service_local
    return app  # type: ignore[return-value]
