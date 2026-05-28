# =============================================================================
# File: test_demo_seed.py
# Version: 1
# Path: ay_platform_core/tests/integration/c2_auth/test_demo_seed.py
# Description: Verifies the C2 manual-test demo seed bootstrap path.
#              When `auth_mode=local` AND `demo_seed_enabled=True`, the
#              C2 lifespan SHALL idempotently provision :
#                - 1 tenant (tenant-test)
#                - 4 users (super-root / tenant-admin / project-editor /
#                  project-viewer) with the right global roles
#                - 1 project (project-test) under that tenant
#                - 2 project grants (editor + viewer)
#              All four users SHALL be able to login afterwards.
#              The seed is a no-op when `demo_seed_enabled=False` or
#              `auth_mode != local`.
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
from ay_platform_core.c2_auth.main import _ensure_demo_seed
from ay_platform_core.c2_auth.models import RBACGlobalRole
from ay_platform_core.c2_auth.router import router
from ay_platform_core.c2_auth.service import AuthService, get_service
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

_JWT_SECRET = "demo-seed-test-secret-key-32ch!!"


@pytest.fixture(scope="function")
def seed_repo(arango_container: ArangoEndpoint) -> Iterator[AuthRepository]:
    """Isolated AuthRepository — pristine DB per test."""
    db_name = f"c2_demo_seed_{uuid.uuid4().hex[:8]}"
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


def _seed_config_enabled() -> AuthConfig:
    """Local mode + demo seed flag on. Other defaults."""
    return AuthConfig.model_validate(
        {
            "auth_mode": "local",
            "jwt_secret_key": _JWT_SECRET,
            "platform_environment": "testing",
            "demo_seed_enabled": True,
        }
    )


@pytest.mark.asyncio
async def test_seed_creates_tenant_users_project_grants(
    seed_repo: AuthRepository,
) -> None:
    """Happy path : seed runs once on a pristine DB, every entity is
    in place with the expected role / scope."""
    cfg = _seed_config_enabled()
    # Pristine — none of the seed records exist yet.
    assert await seed_repo.get_tenant(cfg.demo_seed_tenant_id) is None
    assert await seed_repo.get_project(cfg.demo_seed_project_id) is None
    assert await seed_repo.get_user_by_username("superroot") is None

    await _ensure_demo_seed(seed_repo, cfg)

    # Tenant
    tenant = await seed_repo.get_tenant(cfg.demo_seed_tenant_id)
    assert tenant is not None
    assert tenant["name"] == cfg.demo_seed_tenant_name

    # Users — 4 distinct entries, correct global roles
    expected_users = {
        "superroot": RBACGlobalRole.TENANT_MANAGER,
        "tenant-admin": RBACGlobalRole.ADMIN,
        "project-editor": RBACGlobalRole.USER,
        "project-viewer": RBACGlobalRole.USER,
    }
    for username, expected_role in expected_users.items():
        user = await seed_repo.get_user_by_username(username)
        assert user is not None, f"missing seeded user {username!r}"
        assert expected_role in user.roles, (
            f"user {username!r} missing role {expected_role.value!r}"
        )
        # Argon2id hash applied — the password is never stored plain.
        assert user.argon2id_hash != "dev-superroot"
        assert user.argon2id_hash != "dev-tenant"

    # Tenant binding : super-root is cross-tenant ('default'); the
    # other three live in tenant-test.
    superroot = await seed_repo.get_user_by_username("superroot")
    assert superroot is not None
    assert superroot.tenant_id == "default"
    for tenanted in ("tenant-admin", "project-editor", "project-viewer"):
        u = await seed_repo.get_user_by_username(tenanted)
        assert u is not None
        assert u.tenant_id == cfg.demo_seed_tenant_id

    # Project
    project = await seed_repo.get_project(cfg.demo_seed_project_id)
    assert project is not None
    assert project["tenant_id"] == cfg.demo_seed_tenant_id

    # Project grants — editor + viewer, no owner.
    editor_scopes = await seed_repo.get_project_scopes("demo-project-editor")
    viewer_scopes = await seed_repo.get_project_scopes("demo-project-viewer")
    assert "project_editor" in editor_scopes.get(cfg.demo_seed_project_id, [])
    assert "project_viewer" in viewer_scopes.get(cfg.demo_seed_project_id, [])
    # Tenant-admin SHALL NOT have a project grant — admin role
    # already gives full tenant-scoped access.
    admin_scopes = await seed_repo.get_project_scopes("demo-tenant-admin")
    assert admin_scopes == {}


@pytest.mark.asyncio
async def test_seed_is_idempotent(seed_repo: AuthRepository) -> None:
    """Running the seed twice does not duplicate records nor mutate
    existing hashes (no rehash on re-seed)."""
    cfg = _seed_config_enabled()
    await _ensure_demo_seed(seed_repo, cfg)
    first_user = await seed_repo.get_user_by_username("project-editor")
    assert first_user is not None
    first_hash = first_user.argon2id_hash

    await _ensure_demo_seed(seed_repo, cfg)
    second_user = await seed_repo.get_user_by_username("project-editor")
    assert second_user is not None
    # Same id, same hash — no insert duplication, no password rehash.
    assert second_user.user_id == first_user.user_id
    assert second_user.argon2id_hash == first_hash

    # Tenant + project still single records.
    tenant = await seed_repo.get_tenant(cfg.demo_seed_tenant_id)
    assert tenant is not None
    project = await seed_repo.get_project(cfg.demo_seed_project_id)
    assert project is not None


@pytest.mark.asyncio
async def test_seed_no_op_when_flag_disabled(
    seed_repo: AuthRepository,
) -> None:
    """Default config — seed off. Nothing happens regardless of the
    auth mode."""
    cfg = AuthConfig.model_validate(
        {
            "auth_mode": "local",
            "jwt_secret_key": _JWT_SECRET,
            "platform_environment": "testing",
            # demo_seed_enabled defaults to False
        }
    )
    await _ensure_demo_seed(seed_repo, cfg)

    assert await seed_repo.get_tenant(cfg.demo_seed_tenant_id) is None
    assert await seed_repo.get_project(cfg.demo_seed_project_id) is None
    assert await seed_repo.get_user_by_username("superroot") is None


@pytest.mark.asyncio
async def test_seed_no_op_when_auth_mode_not_local(
    seed_repo: AuthRepository,
) -> None:
    """`auth_mode=none` (or sso) makes the seed a no-op even when the
    flag is on — the seeded passwords would be dead weight without
    local-mode authentication."""
    cfg = AuthConfig.model_validate(
        {
            "auth_mode": "none",
            "jwt_secret_key": _JWT_SECRET,
            "platform_environment": "testing",
            "demo_seed_enabled": True,
        }
    )
    await _ensure_demo_seed(seed_repo, cfg)
    assert await seed_repo.get_user_by_username("superroot") is None


@pytest.mark.asyncio
async def test_seeded_users_can_login(seed_repo: AuthRepository) -> None:
    """End-to-end : the four seeded users SHALL be able to obtain a
    JWT via /auth/login with the credentials from `AuthConfig`."""
    cfg = _seed_config_enabled()
    await _ensure_demo_seed(seed_repo, cfg)

    service = AuthService(cfg, seed_repo)
    app = FastAPI()
    app.include_router(router, prefix="/auth")
    app.dependency_overrides[get_service] = lambda: service

    creds = [
        (cfg.demo_seed_superroot_username, cfg.demo_seed_superroot_password),
        (cfg.demo_seed_tenant_admin_username, cfg.demo_seed_tenant_admin_password),
        (cfg.demo_seed_project_editor_username, cfg.demo_seed_project_editor_password),
        (cfg.demo_seed_project_viewer_username, cfg.demo_seed_project_viewer_password),
    ]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client_http:
        for username, password in creds:
            resp = await client_http.post(
                "/auth/login",
                json={"username": username, "password": password},
            )
            assert resp.status_code == 200, (
                f"login failed for {username!r}: {resp.text}"
            )
            payload = resp.json()
            assert "access_token" in payload
            assert payload["token_type"].lower() == "bearer"
