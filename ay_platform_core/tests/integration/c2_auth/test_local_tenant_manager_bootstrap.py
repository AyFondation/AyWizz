# =============================================================================
# File: test_local_tenant_manager_bootstrap.py
# Version: 1
# Path: ay_platform_core/tests/integration/c2_auth/test_local_tenant_manager_bootstrap.py
# Description: Verifies the C2 application tenant_manager bootstrap path —
#              parallel to `test_local_admin_bootstrap.py` but for the
#              super-root role (R-100-118 v2 + E-100-002 v2).
#
#              Behaviour pinned:
#                1. When auth_mode=local AND both
#                   `local_tenant_manager_*` fields are non-empty, the
#                   helper creates a user with role TENANT_MANAGER.
#                2. Idempotent: re-running the helper does not duplicate.
#                3. No-op when either field is empty (single-tenant
#                   deployment opt-out).
#                4. No-op when auth_mode != "local".
#                5. The bootstrapped user can `/auth/login` and obtain a
#                   JWT (proves password was hashed correctly).
#
# @relation validates:R-100-118
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
from ay_platform_core.c2_auth.main import _ensure_local_tenant_manager
from ay_platform_core.c2_auth.router import router
from ay_platform_core.c2_auth.service import AuthService, get_service
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

_JWT_SECRET = "integration-test-secret-key-32ch!"
_TM_USER = "platform-admin-int"
_TM_PASSWORD = "tm-int-password"


@pytest.fixture(scope="function")
def tm_repo(arango_container: ArangoEndpoint) -> Iterator[AuthRepository]:
    """Isolated AuthRepository — pristine DB, no users yet."""
    db_name = f"c2_tm_test_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db(
        "_system",
        username=arango_container.username,
        password=arango_container.password,
    )
    sys_db.create_database(db_name)
    repo = AuthRepository.from_config(
        arango_container.url,
        db_name,
        arango_container.username,
        arango_container.password,
    )
    repo._ensure_collections_sync()
    try:
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


def _build_config(
    *,
    auth_mode: str = "local",
    tm_username: str = _TM_USER,
    tm_password: str = _TM_PASSWORD,
) -> AuthConfig:
    return AuthConfig.model_validate(
        {
            "auth_mode": auth_mode,
            "jwt_secret_key": _JWT_SECRET,
            "platform_environment": "testing",
            "local_admin_username": "admin-not-relevant",
            "local_admin_password": "x",
            "local_tenant_manager_username": tm_username,
            "local_tenant_manager_password": tm_password,
        }
    )


@pytest.mark.asyncio
async def test_tenant_manager_user_is_created_with_correct_role(
    tm_repo: AuthRepository,
) -> None:
    cfg = _build_config()
    assert await tm_repo.get_user_by_username(_TM_USER) is None

    await _ensure_local_tenant_manager(tm_repo, cfg)

    user = await tm_repo.get_user_by_username(_TM_USER)
    assert user is not None
    assert user.username == _TM_USER
    role_values = [r.value for r in user.roles]
    assert "tenant_manager" in role_values, (
        f"expected tenant_manager role, got {role_values}"
    )
    # Hash applied (not plaintext).
    assert user.argon2id_hash != _TM_PASSWORD


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(tm_repo: AuthRepository) -> None:
    cfg = _build_config()
    await _ensure_local_tenant_manager(tm_repo, cfg)
    first = await tm_repo.get_user_by_username(_TM_USER)
    assert first is not None
    first_hash = first.argon2id_hash

    await _ensure_local_tenant_manager(tm_repo, cfg)
    second = await tm_repo.get_user_by_username(_TM_USER)
    assert second is not None
    assert second.user_id == first.user_id
    assert second.argon2id_hash == first_hash, (
        "re-bootstrap SHALL NOT rehash"
    )


@pytest.mark.asyncio
async def test_no_op_when_username_empty(tm_repo: AuthRepository) -> None:
    """Empty username = opt-out of tenant_manager bootstrap (single-
    tenant deployment relies on admin alone)."""
    cfg = _build_config(tm_username="")
    await _ensure_local_tenant_manager(tm_repo, cfg)
    assert await tm_repo.get_user_by_username(_TM_USER) is None


@pytest.mark.asyncio
async def test_no_op_when_password_empty(tm_repo: AuthRepository) -> None:
    cfg = _build_config(tm_password="")
    await _ensure_local_tenant_manager(tm_repo, cfg)
    assert await tm_repo.get_user_by_username(_TM_USER) is None


@pytest.mark.asyncio
async def test_no_op_when_auth_mode_not_local(
    tm_repo: AuthRepository,
) -> None:
    cfg = _build_config(auth_mode="none")
    await _ensure_local_tenant_manager(tm_repo, cfg)
    assert await tm_repo.get_user_by_username(_TM_USER) is None


@pytest.mark.asyncio
async def test_tenant_manager_can_login_after_bootstrap(
    tm_repo: AuthRepository,
) -> None:
    """End-to-end: bootstrap, then POST /auth/login with the seeded
    creds, expect a JWT back. Proves password hashing round-trips."""
    cfg = _build_config()
    await _ensure_local_tenant_manager(tm_repo, cfg)

    service = AuthService(cfg, tm_repo)
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_service] = lambda: service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/auth/login",
            json={"username": _TM_USER, "password": _TM_PASSWORD},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("access_token"), f"login response missing token: {body}"
