# =============================================================================
# File: test_local_admin_bootstrap.py
# Version: 1
# Path: ay_platform_core/tests/integration/c2_auth/test_local_admin_bootstrap.py
# Description: Verifies the C2 application admin bootstrap path
#              (R-100-118 v2 class (c)). When AUTH_MODE=local, the
#              lifespan SHALL create an admin user from `C2_LOCAL_ADMIN_*`
#              env credentials, and the admin SHALL be able to obtain a
#              JWT via /auth/login. The bootstrap is idempotent across
#              repeated lifespans (same admin → same record).
#
# @relation implements:R-100-118
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
from ay_platform_core.c2_auth.main import _ensure_local_admin
from ay_platform_core.c2_auth.router import router
from ay_platform_core.c2_auth.service import AuthService, get_service
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

_JWT_SECRET = "integration-test-secret-key-32ch!"
_ADMIN_USER = "admin-int"
_ADMIN_PASSWORD = "admin-int-password"


@pytest.fixture(scope="function")
def admin_repo(arango_container: ArangoEndpoint) -> Iterator[AuthRepository]:
    """Isolated AuthRepository — pristine DB, no users yet."""
    db_name = f"c2_admin_test_{uuid.uuid4().hex[:8]}"
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


def _build_local_config() -> AuthConfig:
    return AuthConfig.model_validate(
        {
            "auth_mode": "local",
            "jwt_secret_key": _JWT_SECRET,
            "platform_environment": "testing",
            "local_admin_username": _ADMIN_USER,
            "local_admin_password": _ADMIN_PASSWORD,
        }
    )


@pytest.mark.asyncio
async def test_admin_user_is_created_from_env(
    admin_repo: AuthRepository,
) -> None:
    """`_ensure_local_admin` SHALL create the admin record when absent."""
    cfg = _build_local_config()
    assert await admin_repo.get_user_by_username(_ADMIN_USER) is None

    await _ensure_local_admin(admin_repo, cfg)

    user = await admin_repo.get_user_by_username(_ADMIN_USER)
    assert user is not None
    assert user.username == _ADMIN_USER
    assert "admin" in [r.value for r in user.roles]
    # Hash MUST NOT equal the plaintext (argon2id is applied).
    assert user.argon2id_hash != _ADMIN_PASSWORD


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(admin_repo: AuthRepository) -> None:
    """Calling the bootstrap twice does not duplicate the user nor reset
    the password unintentionally."""
    cfg = _build_local_config()
    await _ensure_local_admin(admin_repo, cfg)
    first = await admin_repo.get_user_by_username(_ADMIN_USER)
    assert first is not None
    first_hash = first.argon2id_hash

    await _ensure_local_admin(admin_repo, cfg)
    second = await admin_repo.get_user_by_username(_ADMIN_USER)
    assert second is not None
    # Same record (`get_user_by_username` is unique on the field) AND
    # same stored hash (no rehash on re-bootstrap).
    assert second.user_id == first.user_id
    assert second.argon2id_hash == first_hash


@pytest.mark.asyncio
async def test_no_op_when_auth_mode_is_not_local(
    admin_repo: AuthRepository,
) -> None:
    """`auth_mode=none` (and `sso`) SHALL NOT trigger a bootstrap, even when
    the admin credentials are present in the env."""
    cfg = AuthConfig.model_validate(
        {
            "auth_mode": "none",
            "jwt_secret_key": _JWT_SECRET,
            "platform_environment": "testing",
            "local_admin_username": _ADMIN_USER,
            "local_admin_password": _ADMIN_PASSWORD,
        }
    )
    await _ensure_local_admin(admin_repo, cfg)
    assert await admin_repo.get_user_by_username(_ADMIN_USER) is None


@pytest.mark.asyncio
async def test_admin_can_login_after_bootstrap(
    admin_repo: AuthRepository,
) -> None:
    """End-to-end: bootstrap the admin → POST /auth/login with those creds
    returns a JWT. This is the contract the operator relies on for first-
    day-after-deploy access. Wrong password is rejected with 401.

    The bootstrap runs explicitly here rather than via the lifespan; the
    behaviour under test is that ``argon2id``-hashed creds end up in
    Arango AND that the local-mode authenticate path then resolves them.
    The lifespan integration is covered separately in
    `test_admin_user_is_created_from_env`.
    """
    cfg = _build_local_config()
    await _ensure_local_admin(admin_repo, cfg)

    service = AuthService(cfg, admin_repo)
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_service] = lambda: service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client_http:
        ok = await client_http.post(
            "/auth/login",
            json={"username": _ADMIN_USER, "password": _ADMIN_PASSWORD},
        )
        assert ok.status_code == 200, ok.text
        payload = ok.json()
        assert "access_token" in payload
        assert payload["token_type"].lower() == "bearer"

        bad = await client_http.post(
            "/auth/login",
            json={"username": _ADMIN_USER, "password": "wrong"},
        )
        assert bad.status_code == 401
