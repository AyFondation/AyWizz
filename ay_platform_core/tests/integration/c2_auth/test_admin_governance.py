# =============================================================================
# File: test_admin_governance.py
# Version: 4
# Path: ay_platform_core/tests/integration/c2_auth/test_admin_governance.py
# Description: Integration tests for the TENANT operator role `admin`
#              (= tenant_admin, E-100-002 v7) on the project governance surface.
#              It mirrors `platform_manager` but is CONFINED to its own tenant:
#              list/status/ACL/grant/revoke work for its tenant's projects,
#              and are refused (403) on another tenant's project. It also
#              cannot reach platform-only surfaces (tenant CRUD). `admin` is
#              content-blind — governance only.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from ay_platform_core.c2_auth.models import (
    LoginRequest,
    ProjectCreate,
    RBACGlobalRole,
    RBACProjectRole,
    TenantCreate,
    UserCreateRequest,
)
from ay_platform_core.c2_auth.service import AuthService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _client(app: httpx.ASGITransport) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed(service: AuthService) -> tuple[str, str]:
    """Two tenants each with a project + a admin on t-acme. Returns
    the admin's bearer token AND the seeded member's user_id."""
    await service.create_tenant(TenantCreate(tenant_id="t-acme", name="Acme"))
    await service.create_tenant(TenantCreate(tenant_id="t-other", name="Other"))
    await service.create_project(
        ProjectCreate(project_id="proj-acme", name="Acme P"),
        tenant_id="t-acme",
        actor_id="system",
    )
    await service.create_project(
        ProjectCreate(project_id="proj-other", name="Other P"),
        tenant_id="t-other",
        actor_id="system",
    )
    await service.create_user(
        UserCreateRequest(
            username="tm-acme",
            password="tm-pass-12!",
            tenant_id="t-acme",
            roles=[RBACGlobalRole.ADMIN],
        )
    )
    # A member user in t-acme to grant/revoke.
    dev = await service.create_user(
        UserCreateRequest(username="dev", password="dev-pass-12!", tenant_id="t-acme")
    )
    token = await service.issue_token(
        LoginRequest(username="tm-acme", password="tm-pass-12!")
    )
    return token.access_token, dev.user_id


async def test_admin_governs_own_tenant(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token, dev_id = await _seed(auth_service_local)
    h = {"Authorization": f"Bearer {token}"}

    async with _client(local_app) as c:
        # List is confined to its own tenant (proj-other absent).
        r = await c.get("/admin/projects", headers=h)
        assert r.status_code == 200, r.text
        ids = {p["project_id"] for p in r.json()["items"]}
        assert ids == {"proj-acme"}, ids

        # Lifecycle status on its own project round-trips.
        r = await c.post("/admin/projects/proj-acme/deactivate", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "inactive"
        r = await c.post("/admin/projects/proj-acme/activate", headers=h)
        assert r.json()["status"] == "active"

        # ACL grant + revoke on its own project.
        r = await c.post(
            f"/admin/projects/proj-acme/members/{dev_id}",
            json={"role": RBACProjectRole.EDITOR.value},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert any(m["user_id"] == dev_id for m in r.json()["members"])
        r = await c.delete(
            f"/admin/projects/proj-acme/members/{dev_id}", headers=h
        )
        assert r.status_code == 200 and r.json()["members"] == []


async def test_admin_refused_on_another_tenant(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token, _ = await _seed(auth_service_local)
    h = {"Authorization": f"Bearer {token}"}
    async with _client(local_app) as c:
        # Cross-tenant project → 403 on every per-project governance action.
        for method, path in [
            ("post", "/admin/projects/proj-other/deactivate"),
            ("post", "/admin/projects/proj-other/archive"),
            ("get", "/admin/projects/proj-other/members"),
        ]:
            r = await getattr(c, method)(path, headers=h)
            assert r.status_code == 403, f"{path} → {r.status_code} {r.text}"


async def test_admin_manages_own_tenant_users(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token, dev_id = await _seed(auth_service_local)
    # A user in the OTHER tenant the admin must not see or touch.
    other = await auth_service_local.create_user(
        UserCreateRequest(
            username="dev-other", password="dev-pass-12!", tenant_id="t-other"
        )
    )
    h = {"Authorization": f"Bearer {token}"}
    async with _client(local_app) as c:
        # List is confined to its own tenant (t-other users absent).
        r = await c.get("/admin/users", headers=h)
        assert r.status_code == 200, r.text
        tenants = {u["tenant_id"] for u in r.json()["items"]}
        assert tenants == {"t-acme"}, tenants

        # Deactivate + reactivate a user of its own tenant round-trips.
        r = await c.post(f"/admin/users/{dev_id}/deactivate", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "disabled"
        r = await c.post(f"/admin/users/{dev_id}/reactivate", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "active"

        # A user in another tenant → 403 on every per-user governance action.
        r = await c.post(f"/admin/users/{other.user_id}/deactivate", headers=h)
        assert r.status_code == 403, r.text


async def test_admin_user_crud_is_tenant_isolated(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    """E-100-002 v7: `/auth/users` CRUD is confined to the admin's tenant.
    Create is forced into the caller's tenant; get/delete on a cross-tenant
    user is refused (403)."""
    token, _ = await _seed(auth_service_local)
    other = await auth_service_local.create_user(
        UserCreateRequest(
            username="victim", password="victim-pass-12!", tenant_id="t-other"
        )
    )
    h = {"Authorization": f"Bearer {token}"}
    async with _client(local_app) as c:
        # Create with a spoofed foreign tenant → forced back to t-acme.
        r = await c.post(
            "/auth/users",
            json={
                "username": "planted",
                "password": "planted-pass-12!",
                "tenant_id": "t-other",
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        assert r.json()["tenant_id"] == "t-acme", r.json()

        # Read + delete of a user in another tenant → 403.
        r = await c.get(f"/auth/users/{other.user_id}", headers=h)
        assert r.status_code == 403, r.text
        r = await c.delete(f"/auth/users/{other.user_id}", headers=h)
        assert r.status_code == 403, r.text


async def test_admin_sees_user_project_access(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    """E-100-002 v7 reverse ACL view: an admin can list a tenant user's project
    grants (project_id + role), scoped to its own tenant."""
    token, dev_id = await _seed(auth_service_local)
    # Grant the seeded member an editor role on the tenant's project.
    await auth_service_local.grant_project_access(
        "proj-acme", dev_id, RBACProjectRole.EDITOR, actor_id="system"
    )
    h = {"Authorization": f"Bearer {token}"}
    async with _client(local_app) as c:
        r = await c.get(f"/admin/users/{dev_id}/projects", headers=h)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [(i["project_id"], i["role"]) for i in items] == [
            ("proj-acme", "project_editor")
        ]

        # A user in another tenant → 403 (scoped).
        other = await auth_service_local.create_user(
            UserCreateRequest(
                username="dev-elsewhere", password="dev-pass-12!", tenant_id="t-other"
            )
        )
        denied = await c.get(f"/admin/users/{other.user_id}/projects", headers=h)
        assert denied.status_code == 403, denied.text


async def test_admin_cannot_reach_platform_surface(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token, _ = await _seed(auth_service_local)
    h = {"Authorization": f"Bearer {token}"}
    async with _client(local_app) as c:
        # Tenant CRUD is platform_manager only.
        r = await c.get("/admin/tenants", headers=h)
        assert r.status_code == 403, r.text
