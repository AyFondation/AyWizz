# =============================================================================
# File: test_project_governance.py
# Version: 1
# Path: ay_platform_core/tests/integration/c2_auth/test_project_governance.py
# Description: Integration tests for the C2 project GOVERNANCE surface
#              (E-100-002 v4) — the platform_manager platform operator's
#              cross-tenant control over the project governance object:
#              list all projects, lifecycle status (activate/deactivate/
#              archive), ACL read, and cross-tenant grant/revoke. Verifies
#              the operator can act WITHOUT any tenant-content access, and
#              that a non-operator is refused.
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


async def _seed_platform_manager(service: AuthService) -> str:
    await service.create_user(
        UserCreateRequest(
            username="root-tm",
            password="tm-pass-12!",
            tenant_id="t-root",
            roles=[RBACGlobalRole.PLATFORM_MANAGER],
        )
    )
    token = await service.issue_token(
        LoginRequest(username="root-tm", password="tm-pass-12!")
    )
    return token.access_token


async def _seed_project_and_user(service: AuthService) -> str:
    """Seed a tenant + project + one member user; return the user's id."""
    await service.create_tenant(TenantCreate(tenant_id="t-acme", name="Acme"))
    await service.create_project(
        ProjectCreate(project_id="proj-alpha", name="Alpha"),
        tenant_id="t-acme",
        actor_id="system",
    )
    dev = await service.create_user(
        UserCreateRequest(
            username="dev", password="dev-pass-12!", tenant_id="t-acme"
        )
    )
    return dev.user_id


async def test_project_governance_full_flow(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    tm = await _seed_platform_manager(auth_service_local)
    dev_id = await _seed_project_and_user(auth_service_local)
    h = {"Authorization": f"Bearer {tm}"}

    async with _client(local_app) as c:
        # 1. Cross-tenant list surfaces the project with its status (default active).
        r = await c.get("/admin/projects", headers=h)
        assert r.status_code == 200, r.text
        proj = next(
            p for p in r.json()["items"] if p["project_id"] == "proj-alpha"
        )
        assert proj["tenant_id"] == "t-acme" and proj["status"] == "active"

        # 2. Lifecycle status transitions round-trip.
        r = await c.post("/admin/projects/proj-alpha/deactivate", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "inactive"
        r = await c.post("/admin/projects/proj-alpha/archive", headers=h)
        assert r.json()["status"] == "archived"
        r = await c.post("/admin/projects/proj-alpha/activate", headers=h)
        assert r.json()["status"] == "active"

        # 3. ACL starts empty; grant then revoke are reflected + audited.
        r = await c.get("/admin/projects/proj-alpha/members", headers=h)
        assert r.status_code == 200 and r.json()["members"] == []

        r = await c.post(
            f"/admin/projects/proj-alpha/members/{dev_id}",
            json={"role": RBACProjectRole.EDITOR.value},
            headers=h,
        )
        assert r.status_code == 200, r.text
        members = r.json()["members"]
        assert any(
            m["user_id"] == dev_id and m["role"] == "project_editor"
            for m in members
        )

        r = await c.delete(
            f"/admin/projects/proj-alpha/members/{dev_id}", headers=h
        )
        assert r.status_code == 200 and r.json()["members"] == []

    # 4. The audit trail recorded the governance actions.
    audit = list(auth_service_local._repo._db.collection("c2_audit").all())  # type: ignore[union-attr,arg-type]
    actions = {rec["action"] for rec in audit}
    assert {"project.status", "access.grant", "access.revoke"} <= actions


async def test_project_governance_denied_without_operator_role(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    # A plain user (no platform_manager) is refused the governance surface.
    await auth_service_local.create_user(
        UserCreateRequest(
            username="plain", password="plain-pass-1!", tenant_id="t-1"
        )
    )
    token = await auth_service_local.issue_token(
        LoginRequest(username="plain", password="plain-pass-1!")
    )
    async with _client(local_app) as c:
        r = await c.get(
            "/admin/projects",
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
    assert r.status_code == 403
