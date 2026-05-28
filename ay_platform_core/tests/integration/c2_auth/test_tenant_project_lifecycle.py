# =============================================================================
# File: test_tenant_project_lifecycle.py
# Version: 2
# Path: ay_platform_core/tests/integration/c2_auth/test_tenant_project_lifecycle.py
# Description: Phase A integration tests — tenant + project lifecycle and
#              membership grants. Round-trip through real ArangoDB:
#              tenant_manager creates a tenant, admin creates a project
#              in it, project_owner grants project_editor on a user,
#              that user's JWT issued at next login carries the
#              project_scopes.
#
#              v2: adds smoke tests for GET / PATCH `/api/v1/projects/
#              {pid}` (effective `system_prompt` resolution + override
#              persistence) and GET / PUT `/api/v1/users/me/preferences`
#              (trigram + user prompt round-trip through C2's prefs
#              collection).
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c2_auth.admin_router import router as c2_admin_router
from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.models import (
    JWTClaims,
    LoginRequest,
    RBACGlobalRole,
    UserCreateRequest,
)
from ay_platform_core.c2_auth.preferences_router import router as c2_preferences_router
from ay_platform_core.c2_auth.projects_router import router as c2_projects_router
from ay_platform_core.c2_auth.router import router as c2_router
from ay_platform_core.c2_auth.service import AuthService
from ay_platform_core.c2_auth.service import get_service as c2_get_service
from tests.fixtures.containers import (
    ArangoEndpoint,
    cleanup_arango_database,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


# ---------------------------------------------------------------------------
# Fixture — fresh C2 stack per test for isolation.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def c2_stack(arango_container: ArangoEndpoint) -> AsyncIterator[tuple[FastAPI, AuthService]]:
    db_name = f"c2_phasea_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    repo = AuthRepository.from_config(
        arango_container.url, db_name, "root", arango_container.password,
    )
    repo._ensure_collections_sync()
    config = AuthConfig.model_validate(
        {
            "auth_mode": "local",
            "jwt_secret_key": "phase-a-test-secret-32-chars-min!",
            "platform_environment": "testing",
            # Non-empty user-prompt default lets the prefs round-trip
            # test assert `user_prompt_is_default=True` returns this
            # exact text when no override has been stored.
            "default_user_prompt": "Be precise. Do not invent.",
            "default_project_prompt": "",
        }
    )
    service = AuthService(config, repo)
    app = FastAPI()
    app.include_router(c2_router, prefix="/auth")
    app.include_router(c2_admin_router, prefix="/admin")
    app.include_router(c2_projects_router, prefix="/api/v1/projects")
    app.include_router(c2_preferences_router, prefix="/api/v1/users/me/preferences")
    app.dependency_overrides[c2_get_service] = lambda: service
    try:
        yield app, service
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-phasea",
    )


async def _bearer_for(service: AuthService, user_id: str, tenant_id: str,
                      roles: list[RBACGlobalRole]) -> dict[str, str]:
    jti = f"jti-{user_id}-{uuid.uuid4().hex[:6]}"
    claims = JWTClaims(
        sub=user_id,
        iat=int(datetime.now(tz=UTC).timestamp()),
        exp=10**12,
        jti=jti,
        auth_mode="local",
        tenant_id=tenant_id,
        roles=roles,
    )
    token = service._sign_jwt(claims)
    if service._repo is not None:
        now = datetime.now(tz=UTC)
        await service._repo.insert_session(
            jti, user_id, now, now.replace(year=now.year + 1),
        )
    return {"Authorization": f"Bearer {token}"}


def _forward_auth(user_id: str, tenant_id: str, roles: tuple[str, ...]) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Tenant-Id": tenant_id,
        "X-User-Roles": ",".join(roles),
    }


# ---------------------------------------------------------------------------
# Tenant lifecycle
# ---------------------------------------------------------------------------


async def test_tenant_manager_creates_lists_deletes_tenant(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    app, service = c2_stack
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    tenant_id = f"tenant-{uuid.uuid4().hex[:6]}"

    async with _client(app) as c:
        # Create
        create = await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": tenant_id, "name": "Acme Inc."},
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["tenant_id"] == tenant_id
        assert body["name"] == "Acme Inc."

        # Re-create → 409 conflict
        dup = await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": tenant_id, "name": "dup"},
        )
        assert dup.status_code == 409

        # List
        listing = await c.get("/admin/tenants", headers=tm_headers)
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert any(t["tenant_id"] == tenant_id for t in items)

        # Delete
        delete = await c.delete(f"/admin/tenants/{tenant_id}", headers=tm_headers)
        assert delete.status_code == 204

        # Re-delete → 404
        re_del = await c.delete(f"/admin/tenants/{tenant_id}", headers=tm_headers)
        assert re_del.status_code == 404


async def test_admin_cannot_create_tenant(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """Tenant creation is reserved for `tenant_manager`. `admin` (tenant
    admin) must be rejected with 403."""
    app, service = c2_stack
    admin_headers = await _bearer_for(
        service, "u-adm", "tenant-x", [RBACGlobalRole.ADMIN],
    )
    async with _client(app) as c:
        response = await c.post(
            "/admin/tenants",
            headers=admin_headers,
            json={"tenant_id": "blocked", "name": "blocked"},
        )
    assert response.status_code == 403
    assert "tenant_manager" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Project lifecycle + member grants
# ---------------------------------------------------------------------------


async def test_full_lifecycle_tenant_project_member_grant(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """End-to-end: tenant_manager → tenant; admin → project; admin →
    grant project_editor to a fresh user; user logs in (via
    issue_token); their JWT carries the project_scopes claim — proving
    the grant is reflected in the auth flow."""
    app, service = c2_stack
    tenant_id = f"tenant-{uuid.uuid4().hex[:6]}"
    project_id = f"proj-{uuid.uuid4().hex[:6]}"
    member_username = f"editor-{uuid.uuid4().hex[:6]}@phase-a.test"
    member_password = "PhaseAEditor1!"

    # 1. tenant_manager creates the tenant.
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    async with _client(app) as c:
        create_t = await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": tenant_id, "name": tenant_id},
        )
        assert create_t.status_code == 201

    # 2. admin (tenant-scoped) creates a project in their tenant via
    #    forward-auth headers.
    admin_fa = _forward_auth("u-admin", tenant_id, ("admin",))
    async with _client(app) as c:
        create_p = await c.post(
            "/api/v1/projects",
            headers=admin_fa,
            json={"project_id": project_id, "name": "Phase A demo"},
        )
        assert create_p.status_code == 201, create_p.text
        body = create_p.json()
        assert body["project_id"] == project_id
        assert body["tenant_id"] == tenant_id
        assert body["created_by"] == "u-admin"

    # 3. Seed a fresh user in the tenant (so they exist for the grant).
    member_user = await service.create_user(
        UserCreateRequest(
            username=member_username,
            password=member_password,
            tenant_id=tenant_id,
            roles=[RBACGlobalRole.USER],
        )
    )

    # 4. admin grants project_editor on (project, member_user).
    async with _client(app) as c:
        grant = await c.post(
            f"/api/v1/projects/{project_id}/members/{member_user.user_id}",
            headers=admin_fa,
            json={"role": "project_editor"},
        )
        assert grant.status_code == 204, grant.text

    # 5. member logs in; the issued JWT carries project_scopes.
    token_response = await service.issue_token(
        LoginRequest(username=member_username, password=member_password),
    )
    decoded = await service.verify_token(token_response.access_token)
    assert decoded.tenant_id == tenant_id
    assert project_id in decoded.project_scopes
    assert "project_editor" in decoded.project_scopes[project_id]


async def test_project_listing_filtered_by_tenant(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """A user in tenant_a SHALL only see projects of tenant_a; tenant_b's
    projects SHALL NOT leak into the response."""
    app, service = c2_stack
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    async with _client(app) as c:
        for tid in ("tenant-iso-a", "tenant-iso-b"):
            create_t = await c.post(
                "/admin/tenants",
                headers=tm_headers,
                json={"tenant_id": tid, "name": tid},
            )
            assert create_t.status_code == 201, create_t.text

    a_admin = _forward_auth("u-adm-a", "tenant-iso-a", ("admin",))
    b_admin = _forward_auth("u-adm-b", "tenant-iso-b", ("admin",))
    async with _client(app) as c:
        await c.post(
            "/api/v1/projects",
            headers=a_admin,
            json={"project_id": "p-only-a", "name": "tenant_a project"},
        )
        await c.post(
            "/api/v1/projects",
            headers=b_admin,
            json={"project_id": "p-only-b", "name": "tenant_b project"},
        )
        a_list = await c.get("/api/v1/projects", headers=a_admin)
        b_list = await c.get("/api/v1/projects", headers=b_admin)

    a_ids = {p["project_id"] for p in a_list.json()["items"]}
    b_ids = {p["project_id"] for p in b_list.json()["items"]}
    assert "p-only-a" in a_ids and "p-only-b" not in a_ids
    assert "p-only-b" in b_ids and "p-only-a" not in b_ids


async def test_tenant_manager_cannot_list_tenant_projects(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """E-100-002 v2: tenant_manager is content-blind. The
    GET /api/v1/projects endpoint SHALL reject tenant_manager even
    though it would normally accept any authenticated user."""
    app, _service = c2_stack
    tm_fa = _forward_auth("u-tm", "anytenant", ("tenant_manager",))
    async with _client(app) as c:
        response = await c.get("/api/v1/projects", headers=tm_fa)
    assert response.status_code == 403
    assert "tenant_manager" in response.json()["detail"]


async def test_grant_user_in_other_tenant_returns_400(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """The granter cannot bind a user from a foreign tenant onto a
    project in their own tenant — 400 with a clear error."""
    app, service = c2_stack
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    async with _client(app) as c:
        for tid in ("tenant-grant-a", "tenant-grant-b"):
            await c.post(
                "/admin/tenants",
                headers=tm_headers,
                json={"tenant_id": tid, "name": tid},
            )
    a_admin = _forward_auth("u-adm-a", "tenant-grant-a", ("admin",))
    async with _client(app) as c:
        await c.post(
            "/api/v1/projects",
            headers=a_admin,
            json={"project_id": "p-grant-a", "name": "tenant_a project"},
        )
    foreign_user = await service.create_user(
        UserCreateRequest(
            username=f"foreign-{uuid.uuid4().hex[:6]}@phase-a.test",
            password="ForeignPw1!",
            tenant_id="tenant-grant-b",
            roles=[RBACGlobalRole.USER],
        )
    )
    async with _client(app) as c:
        response = await c.post(
            f"/api/v1/projects/p-grant-a/members/{foreign_user.user_id}",
            headers=a_admin,
            json={"role": "project_editor"},
        )
    assert response.status_code == 400
    assert "different tenant" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Project deletion + cascade (gap-fill 2026-04-28)
# ---------------------------------------------------------------------------


async def test_delete_project_cascades_member_grants_and_404s_on_re_delete(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """End-to-end DELETE /api/v1/projects/{pid} :

    - tenant_manager → tenant ; admin → projet + grant editor sur un user.
    - admin DELETE le projet (204).
    - re-DELETE → 404 (no leak via repeated deletes).
    - cross-tenant DELETE (other tenant's admin) → 404.
    - le grant project_editor du user a été cascade (gone from
      role_assignments) — re-creating the same project doesn't
      resurrect stale grants.
    """
    app, service = c2_stack
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    tenant_id = f"tenant-del-{uuid.uuid4().hex[:6]}"
    other_tenant_id = f"tenant-del-other-{uuid.uuid4().hex[:6]}"
    project_id = f"proj-del-{uuid.uuid4().hex[:6]}"

    async with _client(app) as c:
        await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": tenant_id, "name": tenant_id},
        )
        await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": other_tenant_id, "name": other_tenant_id},
        )

    a_admin = _forward_auth("u-adm-del", tenant_id, ("admin",))
    other_admin = _forward_auth(
        "u-adm-other-del", other_tenant_id, ("admin",),
    )

    # Seed: user + project + grant.
    member = await service.create_user(
        UserCreateRequest(
            username=f"member-{uuid.uuid4().hex[:6]}@phase-a.test",
            password="MemberPw1!",
            tenant_id=tenant_id,
            roles=[RBACGlobalRole.USER],
        )
    )
    async with _client(app) as c:
        create = await c.post(
            "/api/v1/projects",
            headers=a_admin,
            json={"project_id": project_id, "name": "to-delete"},
        )
        assert create.status_code == 201
        grant = await c.post(
            f"/api/v1/projects/{project_id}/members/{member.user_id}",
            headers=a_admin,
            json={"role": "project_editor"},
        )
        assert grant.status_code == 204

        # Cross-tenant DELETE → 404 (don't leak existence).
        cross = await c.delete(
            f"/api/v1/projects/{project_id}", headers=other_admin,
        )
        assert cross.status_code == 404

        # Real DELETE → 204.
        delete = await c.delete(
            f"/api/v1/projects/{project_id}", headers=a_admin,
        )
        assert delete.status_code == 204

        # Re-DELETE → 404.
        re_del = await c.delete(
            f"/api/v1/projects/{project_id}", headers=a_admin,
        )
        assert re_del.status_code == 404

        # GET projects list no longer includes it.
        listing = await c.get("/api/v1/projects", headers=a_admin)
        ids = {p["project_id"] for p in listing.json()["items"]}
        assert project_id not in ids

    # Cascade verification: re-create the project and check that the
    # member's old grant is GONE — they need a fresh grant. Since the
    # JWT carries project_scopes from c2_role_assignments, we check
    # by issuing a fresh token for the member and inspecting claims.
    async with _client(app) as c:
        recreate = await c.post(
            "/api/v1/projects",
            headers=a_admin,
            json={"project_id": project_id, "name": "resurrected"},
        )
        assert recreate.status_code == 201

    from ay_platform_core.c2_auth.models import LoginRequest  # noqa: PLC0415
    member_token = await service.issue_token(
        LoginRequest(username=member.username, password="MemberPw1!"),
    )
    member_claims = await service.verify_token(member_token.access_token)
    assert project_id not in member_claims.project_scopes, (
        "stale grant resurrected after project re-creation — cascade failed"
    )


# ---------------------------------------------------------------------------
# v2 — Project read + system_prompt override + user preferences
# ---------------------------------------------------------------------------


async def test_get_project_returns_effective_system_prompt(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """`GET /api/v1/projects/{pid}` is the per-project read for the UX.
    On a freshly-created project (no per-project override) the
    `system_prompt` field SHALL equal the C2-wide default and
    `system_prompt_is_default` SHALL be True."""
    app, service = c2_stack
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    async with _client(app) as c:
        await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": "tenant-sp", "name": "sp"},
        )
        admin_fa = _forward_auth("u-adm", "tenant-sp", ("admin",))
        await c.post(
            "/api/v1/projects",
            headers=admin_fa,
            json={"project_id": "proj-sp", "name": "sp"},
        )
        read = await c.get("/api/v1/projects/proj-sp", headers=admin_fa)
        assert read.status_code == 200, read.text
        body = read.json()
        assert body["project_id"] == "proj-sp"
        # default_project_prompt is empty in the fixture config.
        assert body["system_prompt"] == ""
        assert body["system_prompt_is_default"] is True


async def test_patch_project_sets_and_clears_system_prompt(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """End-to-end PATCH cycle: empty (default) → override → reset
    (empty string clears). Each step's response reflects the new
    state and the `is_default` flag tracks accordingly."""
    app, service = c2_stack
    tm_headers = await _bearer_for(
        service, "u-tm", "platform", [RBACGlobalRole.TENANT_MANAGER],
    )
    async with _client(app) as c:
        await c.post(
            "/admin/tenants",
            headers=tm_headers,
            json={"tenant_id": "tenant-patch", "name": "patch"},
        )
        admin_fa = _forward_auth("u-adm", "tenant-patch", ("admin",))
        await c.post(
            "/api/v1/projects",
            headers=admin_fa,
            json={"project_id": "proj-patch", "name": "patch"},
        )

        # Set an override.
        set_resp = await c.patch(
            "/api/v1/projects/proj-patch",
            headers=admin_fa,
            json={"system_prompt": "Talk like a pirate."},
        )
        assert set_resp.status_code == 200, set_resp.text
        body = set_resp.json()
        assert body["system_prompt"] == "Talk like a pirate."
        assert body["system_prompt_is_default"] is False

        # Re-read confirms persistence.
        read = await c.get("/api/v1/projects/proj-patch", headers=admin_fa)
        assert read.json()["system_prompt"] == "Talk like a pirate."
        assert read.json()["system_prompt_is_default"] is False

        # Clear the override (empty string sentinel).
        clear_resp = await c.patch(
            "/api/v1/projects/proj-patch",
            headers=admin_fa,
            json={"system_prompt": ""},
        )
        assert clear_resp.status_code == 200, clear_resp.text
        body = clear_resp.json()
        assert body["system_prompt"] == ""  # default is empty in fixture
        assert body["system_prompt_is_default"] is True


async def test_user_preferences_round_trip(
    c2_stack: tuple[FastAPI, AuthService],
) -> None:
    """GET initially returns the C2 defaults; PUT writes an override;
    GET reflects the override; PUT with empty strings reverts to
    defaults. Trigram persists as null when not yet stored."""
    app, _service = c2_stack
    user_fa = _forward_auth("u-prefs", "tenant-prefs", ("project_editor",))

    async with _client(app) as c:
        # Initial GET — no record yet, defaults flow through.
        initial = await c.get(
            "/api/v1/users/me/preferences", headers=user_fa,
        )
        assert initial.status_code == 200, initial.text
        body = initial.json()
        assert body["trigram"] is None
        assert body["user_prompt"] == "Be precise. Do not invent."
        assert body["user_prompt_is_default"] is True
        assert body["user_color"] is None

        # PUT all overridable fields → overrides set.
        put_resp = await c.put(
            "/api/v1/users/me/preferences",
            headers=user_fa,
            json={
                "trigram": "PRO",
                "user_prompt": "Answer in French.",
                "user_color": "#3B82F6",
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        body = put_resp.json()
        assert body["trigram"] == "PRO"
        assert body["user_prompt"] == "Answer in French."
        assert body["user_prompt_is_default"] is False
        # Hex colour is normalised to lowercase server-side so
        # downstream consumers can compare without re-casing.
        assert body["user_color"] == "#3b82f6"

        # GET reflects the override.
        re_read = await c.get(
            "/api/v1/users/me/preferences", headers=user_fa,
        )
        assert re_read.json() == body

        # PUT empty strings → clear overrides.
        clear = await c.put(
            "/api/v1/users/me/preferences",
            headers=user_fa,
            json={"trigram": "", "user_prompt": "", "user_color": ""},
        )
        assert clear.status_code == 200, clear.text
        body = clear.json()
        assert body["trigram"] is None
        assert body["user_prompt"] == "Be precise. Do not invent."
        assert body["user_prompt_is_default"] is True
        assert body["user_color"] is None

        # Invalid trigram → 400.
        bad = await c.put(
            "/api/v1/users/me/preferences",
            headers=user_fa,
            json={"trigram": "X!"},
        )
        assert bad.status_code == 400

        # Invalid hex colour → 400.
        bad_color = await c.put(
            "/api/v1/users/me/preferences",
            headers=user_fa,
            json={"user_color": "blue"},
        )
        assert bad_color.status_code == 400
