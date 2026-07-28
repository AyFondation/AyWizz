# =============================================================================
# File: test_project_lifecycle_enforcement.py
# Version: 1
# Path: ay_platform_core/tests/integration/c2_auth/test_project_lifecycle_enforcement.py
# Description: Integration tests for project LIFECYCLE-STATUS enforcement at
#              the C2 `/verify` forward-auth boundary (E-100-002 v4). A
#              request against project CONTENT is refused, independently of
#              the caller's role, when the target project is not `active`:
#              `inactive` blocks every method, `archived` blocks mutations
#              only (read-only freeze). Governance URIs are exempt so the
#              project stays reachable to reactivate / inspect.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from ay_platform_core.c2_auth.models import (
    LoginRequest,
    ProjectCreate,
    ProjectStatus,
    TenantCreate,
    UserCreateRequest,
)
from ay_platform_core.c2_auth.service import AuthService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_CONTENT_URI = "/api/v1/memory/projects/proj-alpha/sources"
_GOVERNANCE_URI = "/api/v1/projects/proj-alpha"  # bare metadata resource


def _client(app: httpx.ASGITransport) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _seed(service: AuthService) -> str:
    """Seed a tenant + project + one member; return the member's bearer token."""
    await service.create_tenant(TenantCreate(tenant_id="t-acme", name="Acme"))
    await service.create_project(
        ProjectCreate(project_id="proj-alpha", name="Alpha"),
        tenant_id="t-acme",
        actor_id="system",
    )
    await service.create_user(
        UserCreateRequest(
            username="dev", password="dev-pass-12!", tenant_id="t-acme"
        )
    )
    token = await service.issue_token(
        LoginRequest(username="dev", password="dev-pass-12!")
    )
    return token.access_token


def _fwd(token: str, uri: str, method: str) -> dict[str, str]:
    """Headers mirroring Traefik forward-auth to `GET /verify`."""
    return {
        "Authorization": f"Bearer {token}",
        "X-Forwarded-Uri": uri,
        "X-Forwarded-Method": method,
    }


async def test_active_project_content_passes(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token = await _seed(auth_service_local)
    async with _client(local_app) as c:
        r = await c.get("/auth/verify", headers=_fwd(token, _CONTENT_URI, "POST"))
    assert r.status_code == 200, r.text


async def test_inactive_project_blocks_every_method(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token = await _seed(auth_service_local)
    await auth_service_local.set_project_status(
        "proj-alpha", ProjectStatus.INACTIVE, actor_id="op"
    )
    async with _client(local_app) as c:
        read = await c.get("/auth/verify", headers=_fwd(token, _CONTENT_URI, "GET"))
        write = await c.get("/auth/verify", headers=_fwd(token, _CONTENT_URI, "POST"))
    assert read.status_code == 403, read.text
    assert write.status_code == 403, write.text
    assert "inactive" in read.json()["detail"]


async def test_archived_project_is_read_only(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token = await _seed(auth_service_local)
    await auth_service_local.set_project_status(
        "proj-alpha", ProjectStatus.ARCHIVED, actor_id="op"
    )
    async with _client(local_app) as c:
        read = await c.get("/auth/verify", headers=_fwd(token, _CONTENT_URI, "GET"))
        write = await c.get(
            "/auth/verify", headers=_fwd(token, _CONTENT_URI, "DELETE")
        )
    assert read.status_code == 200, read.text  # read-only freeze allows reads
    assert write.status_code == 403, write.text
    assert "archived" in write.json()["detail"]


async def test_governance_uri_is_exempt_when_frozen(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    """An inactive project's GOVERNANCE surface stays reachable — otherwise an
    owner could never see the freeze and an operator could never reactivate."""
    token = await _seed(auth_service_local)
    await auth_service_local.set_project_status(
        "proj-alpha", ProjectStatus.INACTIVE, actor_id="op"
    )
    async with _client(local_app) as c:
        meta = await c.get(
            "/auth/verify", headers=_fwd(token, _GOVERNANCE_URI, "PATCH")
        )
    assert meta.status_code == 200, meta.text


async def test_reactivated_project_content_passes_again(
    local_app: httpx.ASGITransport, auth_service_local: AuthService
) -> None:
    token = await _seed(auth_service_local)
    await auth_service_local.set_project_status(
        "proj-alpha", ProjectStatus.INACTIVE, actor_id="op"
    )
    await auth_service_local.set_project_status(
        "proj-alpha", ProjectStatus.ACTIVE, actor_id="op"
    )
    async with _client(local_app) as c:
        r = await c.get("/auth/verify", headers=_fwd(token, _CONTENT_URI, "POST"))
    assert r.status_code == 200, r.text
