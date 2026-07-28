# =============================================================================
# File: test_admin_endpoints.py
# Version: 2
# Path: ay_platform_core/tests/integration/c2_auth/test_admin_endpoints.py
# Description: Integration tests for the C2 admin endpoints (user management
#              + session management). These paths were flagged as uncovered
#              by the coverage audit — the local-login flow tests only cover
#              login/verify/logout, never the admin roster.
#
#              Uses the local-mode service fixture and creates users with
#              admin roles directly via the service facade (the only way
#              to bootstrap an admin without pre-seeding the DB).
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from ay_platform_core.c2_auth.models import (
    LoginRequest,
    RBACGlobalRole,
    ResetPasswordRequest,
    TenantCreate,
    UserCreateRequest,
    UserStatus,
    UserUpdateRequest,
)
from ay_platform_core.c2_auth.service import AuthService


def _client(app: httpx.ASGITransport) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed_admin(service: AuthService) -> str:
    """Create an admin user and return a valid bearer token for it. The admin
    lives in `t-1` — the SAME tenant as the target-user fixtures below — since
    E-100-002 v7 confines an admin's user CRUD to its own tenant (cross-tenant
    isolation is covered by test_admin_governance)."""
    await service.create_user(
        UserCreateRequest(
            username="root-admin",
            password="admin-pass-12!",
            tenant_id="t-1",
            roles=[RBACGlobalRole.ADMIN],
        )
    )
    token = await service.issue_token(
        LoginRequest(username="root-admin", password="admin-pass-12!")
    )
    return token.access_token


# ---------------------------------------------------------------------------
# User management endpoints (admin or tenant_admin)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_user_as_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    async with _client(local_app) as client:
        resp = await client.post(
            "/auth/users",
            json={"username": "bob", "password": "bob-pass-12!", "tenant_id": "t-1"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "bob"
    assert body["status"] == UserStatus.ACTIVE.value


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_user_without_admin_role_denied(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    # Create a normal user and issue a token with only `user` role.
    await auth_service_local.create_user(
        UserCreateRequest(username="plain", password="plain-pass-1!", tenant_id="t-1")
    )
    token = await auth_service_local.issue_token(
        LoginRequest(username="plain", password="plain-pass-1!")
    )
    async with _client(local_app) as client:
        resp = await client.post(
            "/auth/users",
            json={"username": "uninvited", "password": "x-pass-12!", "tenant_id": "t-1"},
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_user_as_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    target = await auth_service_local.create_user(
        UserCreateRequest(username="target", password="target-pass-1!", tenant_id="t-1")
    )
    async with _client(local_app) as client:
        resp = await client.get(
            f"/auth/users/{target.user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["username"] == "target"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_user_roles(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    target = await auth_service_local.create_user(
        UserCreateRequest(username="promotable", password="prom-pass-12!", tenant_id="t-1")
    )
    async with _client(local_app) as client:
        resp = await client.patch(
            f"/auth/users/{target.user_id}",
            json={"roles": [RBACGlobalRole.TENANT_ADMIN.value]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    assert RBACGlobalRole.TENANT_ADMIN.value in resp.json()["roles"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_disable_user_as_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    target = await auth_service_local.create_user(
        UserCreateRequest(username="doomed", password="doom-pass-12!", tenant_id="t-1")
    )
    async with _client(local_app) as client:
        resp = await client.delete(
            f"/auth/users/{target.user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 204
    # Subsequent login with the disabled account is rejected.
    from fastapi import HTTPException  # noqa: PLC0415 — local to the test

    with pytest.raises(HTTPException) as exc_info:
        await auth_service_local.issue_token(
            LoginRequest(username="doomed", password="doom-pass-12!")
        )
    assert exc_info.value.status_code in (401, 403)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reset_password_as_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    target = await auth_service_local.create_user(
        UserCreateRequest(username="forgetful", password="old-pass-12!", tenant_id="t-1")
    )
    async with _client(local_app) as client:
        resp = await client.post(
            f"/auth/users/{target.user_id}/reset-password",
            json={"new_password": "new-pass-34!"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 204
    # New password works.
    token = await auth_service_local.issue_token(
        LoginRequest(username="forgetful", password="new-pass-34!")
    )
    assert token.access_token


# ---------------------------------------------------------------------------
# Session management (admin only)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_sessions_as_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    async with _client(local_app) as client:
        resp = await client.get(
            "/auth/sessions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # The admin's own session SHALL appear in the active-session roster.
    assert len(body) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revoke_session_as_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)
    async with _client(local_app) as client:
        sessions = await client.get(
            "/auth/sessions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        target_session_id = sessions.json()[0]["session_id"]
        resp = await client.delete(
            f"/auth/sessions/{target_session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 204


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sessions_endpoint_requires_admin_not_tenant_admin(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    # `tenant_admin` can manage users but NOT sessions (admin only).
    await auth_service_local.create_user(
        UserCreateRequest(
            username="tadmin",
            password="tadmin-pass-12!",
            tenant_id="t-1",
            roles=[RBACGlobalRole.TENANT_ADMIN],
        )
    )
    tadmin_token = await auth_service_local.issue_token(
        LoginRequest(username="tadmin", password="tadmin-pass-12!")
    )
    async with _client(local_app) as client:
        resp = await client.get(
            "/auth/sessions",
            headers={"Authorization": f"Bearer {tadmin_token.access_token}"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Small direct-service tests for code paths not reachable via HTTP
# (update_user with extra fields, reset_password idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_update_user_status(auth_service_local: AuthService) -> None:
    """Facade-level: update_user accepts a status transition without HTTP."""
    target = await auth_service_local.create_user(
        UserCreateRequest(username="stateful", password="stateful-pass-12!", tenant_id="t-1")
    )
    updated = await auth_service_local.update_user(
        target.user_id, UserUpdateRequest(status=UserStatus.DISABLED)
    )
    assert updated.status == UserStatus.DISABLED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_service_reset_password_idempotent(
    auth_service_local: AuthService,
) -> None:
    created = await auth_service_local.create_user(
        UserCreateRequest(username="repeater", password="old-pass-12!", tenant_id="t-1")
    )
    # Two consecutive resets to the same value both succeed.
    await auth_service_local.reset_password(
        created.user_id, ResetPasswordRequest(new_password="new-pass-34!")
    )
    await auth_service_local.reset_password(
        created.user_id, ResetPasswordRequest(new_password="new-pass-34!")
    )
    token = await auth_service_local.issue_token(
        LoginRequest(username="repeater", password="new-pass-34!")
    )
    assert token.access_token


# ---------------------------------------------------------------------------
# Platform-operator surface (platform_manager — E-100-002 v3, Lot 2)
# ---------------------------------------------------------------------------


async def _seed_platform_manager(service: AuthService) -> str:
    await service.create_user(
        UserCreateRequest(
            username="root-operator",
            password="operator-pass-12!",
            tenant_id="t-platform",
            roles=[RBACGlobalRole.PLATFORM_MANAGER],
        )
    )
    token = await service.issue_token(
        LoginRequest(username="root-operator", password="operator-pass-12!")
    )
    return token.access_token


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deactivate_tenant_blocks_member_login(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    tm_token = await _seed_platform_manager(auth_service_local)
    # A managed tenant + a member of it.
    await auth_service_local.create_tenant(
        TenantCreate(tenant_id="acme", name="Acme")
    )
    await auth_service_local.create_user(
        UserCreateRequest(username="acme-user", password="acme-pass-12!", tenant_id="acme")
    )
    async with _client(local_app) as client:
        # Login works while active.
        ok = await client.post(
            "/auth/login", json={"username": "acme-user", "password": "acme-pass-12!"}
        )
        assert ok.status_code == 200, ok.text

        # platform_manager deactivates the tenant.
        deact = await client.post(
            "/admin/tenants/acme/deactivate",
            headers={"Authorization": f"Bearer {tm_token}"},
        )
        assert deact.status_code == 200, deact.text
        assert deact.json()["active"] is False

        # The member can no longer log in.
        blocked = await client.post(
            "/auth/login", json={"username": "acme-user", "password": "acme-pass-12!"}
        )
        assert blocked.status_code == 403, blocked.text

        # Reactivation restores login.
        react = await client.post(
            "/admin/tenants/acme/reactivate",
            headers={"Authorization": f"Bearer {tm_token}"},
        )
        assert react.status_code == 200 and react.json()["active"] is True
        restored = await client.post(
            "/auth/login", json={"username": "acme-user", "password": "acme-pass-12!"}
        )
    assert restored.status_code == 200, restored.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_lifecycle_requires_platform_manager(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    admin_token = await _seed_admin(auth_service_local)  # admin, NOT platform_manager
    async with _client(local_app) as client:
        resp = await client.post(
            "/admin/tenants/whatever/deactivate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_tenant_user_list_and_deactivate(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    tm_token = await _seed_platform_manager(auth_service_local)
    await auth_service_local.create_user(
        UserCreateRequest(username="u-a", password="u-a-pass-12!", tenant_id="tenant-a")
    )
    await auth_service_local.create_user(
        UserCreateRequest(username="u-b", password="u-b-pass-12!", tenant_id="tenant-b")
    )
    headers = {"Authorization": f"Bearer {tm_token}"}
    async with _client(local_app) as client:
        # Cross-tenant list sees users from BOTH tenants.
        all_users = await client.get("/admin/users", headers=headers)
        assert all_users.status_code == 200, all_users.text
        tenants_seen = {u["tenant_id"] for u in all_users.json()["items"]}
        assert {"tenant-a", "tenant-b"} <= tenants_seen

        # Filter by tenant.
        only_a = await client.get("/admin/users?tenant_id=tenant-a", headers=headers)
        assert only_a.status_code == 200
        assert all(u["tenant_id"] == "tenant-a" for u in only_a.json()["items"])
        target = only_a.json()["items"][0]["user_id"]

        # Deactivate cross-tenant → status disabled → login refused.
        deact = await client.post(f"/admin/users/{target}/deactivate", headers=headers)
        assert deact.status_code == 200 and deact.json()["status"] == "disabled"
        blocked = await client.post(
            "/auth/login", json={"username": "u-a", "password": "u-a-pass-12!"}
        )
        assert blocked.status_code == 403

        # Reactivate → login works again.
        react = await client.post(f"/admin/users/{target}/reactivate", headers=headers)
        assert react.status_code == 200 and react.json()["status"] == "active"
        ok = await client.post(
            "/auth/login", json={"username": "u-a", "password": "u-a-pass-12!"}
        )
    assert ok.status_code == 200, ok.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_oversight_requires_operator(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    """E-100-002 v7: user oversight is an OPERATOR surface. `admin` (tenant
    operator) is now accepted — scoped to its own tenant — whereas a baseline
    `user` with no operator role is still refused 403."""
    admin_token = await _seed_admin(auth_service_local)
    await auth_service_local.create_user(
        UserCreateRequest(
            username="plain-user", password="plain-pass-12!", tenant_id="t-1"
        )
    )
    plain = await auth_service_local.issue_token(
        LoginRequest(username="plain-user", password="plain-pass-12!")
    )
    async with _client(local_app) as client:
        # Tenant operator `admin` → accepted (scoped, v7).
        ok = await client.get(
            "/admin/users", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert ok.status_code == 200, ok.text
        # Baseline user (no operator role) → refused.
        denied = await client.get(
            "/admin/users",
            headers={"Authorization": f"Bearer {plain.access_token}"},
        )
    assert denied.status_code == 403
