# =============================================================================
# File: test_project_content_gate.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_project_content_gate.py
# Description: Tests for `require_project_content_role` — the in-app
#              defence-in-depth floor on project content (E-100-002 v8).
#
#              WHY IT EXISTS. The gateway refuses a project-content request
#              from a caller with no grant on that project, which closed the
#              70-endpoint hole the 2026-10-06 audit found. This dependency
#              is the SECOND line, attached to twelve routers so that a
#              component reached DIRECTLY — a port-forward, a mesh topology,
#              a mistaken `expose:` — still meets a gate, and so that a route
#              nobody has written yet inherits one.
#
#              THREE PROPERTIES, and each has already been wrong once:
#                1. it self-limits to `/projects/<id>/` paths. Two of the
#                   twelve routers are mixed; gating `/api/v1/memory/retrieve`
#                   would break C3's RAG chat path, and gating
#                   `/api/v1/process/*` would break the tenant-level process
#                   surface.
#                2. 401 before 403. A router dependency runs ahead of the
#                   route's own `_require_actor`, so the first version
#                   answered 403 to ANONYMOUS callers — nine `test_anonymous_*`
#                   tests across C5 caught it.
#                3. content-blind global roles are stripped, so `admin` alone
#                   does not pass (E-100-002 v7).
# =============================================================================

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from ay_platform_core.c2_auth.forward_auth import require_project_content_role

pytestmark = pytest.mark.unit


@pytest.fixture
def client() -> TestClient:
    """An app whose router carries the gate, mirroring the real wiring.

    Routes are declared on a router with
    `dependencies=[Depends(require_project_content_role)]` rather than
    per-route, because the ORDER of a router-level dependency relative to
    the route's own is exactly what produced the 401/403 defect. Testing
    the function in isolation would not have caught it.
    """
    router = APIRouter(dependencies=[Depends(require_project_content_role)])

    @router.get("/api/v1/projects/{project_id}/plans")
    async def _plans(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    @router.get("/api/v1/memory/projects/{project_id}/sources")
    async def _sources(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    # Non-project routes sharing the same router — the mixed-router case.
    @router.post("/api/v1/memory/retrieve")
    async def _retrieve() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/api/v1/process/cycles")
    async def _cycles() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/api/v1/memory/health")
    async def _health() -> dict[str, bool]:
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _hdr(user: str | None = "u-1", roles: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if user is not None:
        out["X-User-Id"] = user
    if roles is not None:
        out["X-User-Roles"] = roles
    return out


# ---------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/p1/plans",
        "/api/v1/memory/projects/p1/sources",
    ],
)
def test_identity_without_a_project_role_is_403(client: TestClient, path: str) -> None:
    resp = client.get(path, headers=_hdr(roles="user"))
    assert resp.status_code == 403
    assert "project role" in resp.json()["detail"]


@pytest.mark.parametrize("roles", ["admin", "tenant_admin", "admin,tenant_admin"])
def test_content_blind_global_roles_do_not_pass(
    client: TestClient, roles: str
) -> None:
    """E-100-002 v7: `admin` and `tenant_admin` reach no project content.

    This is the rule the gateway now enforces too; if the two layers
    disagreed, an admin would pass one and be refused by the other, and the
    403 would be unexplainable from either side.
    """
    resp = client.get("/api/v1/projects/p1/plans", headers=_hdr(roles=roles))
    assert resp.status_code == 403


def test_no_roles_header_at_all_is_403_when_an_identity_is_present(
    client: TestClient,
) -> None:
    resp = client.get("/api/v1/projects/p1/plans", headers=_hdr())
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 401 before 403 — the defect this gate shipped with for one iteration
# ---------------------------------------------------------------------------


def test_anonymous_is_401_not_403(client: TestClient) -> None:
    """No identity SHALL answer 401, not 403.

    The first version of the dependency had no `x_user_id` branch, so an
    anonymous caller got 403 — and because a router-level dependency runs
    BEFORE the route's `_require_actor`, that 403 pre-empted the 401 the
    platform answers everywhere else. Nine `test_anonymous_*` tests across
    C5 failed on it.

    It is not a cosmetic difference: 403 to a request carrying no
    credentials states that credentials were evaluated and found wanting,
    which is both untrue and more than an unauthenticated caller should
    learn.
    """
    resp = client.get("/api/v1/projects/p1/plans", headers=_hdr(user=None))
    assert resp.status_code == 401
    assert "X-User-Id" in resp.json()["detail"]


def test_anonymous_on_a_non_project_path_is_not_touched(client: TestClient) -> None:
    """The 401 branch is inside the project-path guard, deliberately.

    `/api/v1/memory/retrieve` has its own `_require_actor`; this gate must
    not pre-empt it, or it would start owning the authentication contract of
    routes it knows nothing about.
    """
    resp = client.post("/api/v1/memory/retrieve", headers=_hdr(user=None))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# What it admits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles",
    ["project_viewer", "project_editor", "project_owner", "admin,project_viewer"],
)
def test_any_project_grant_passes_the_floor(client: TestClient, roles: str) -> None:
    """It is a FLOOR, not a write gate.

    Distinguishing viewer from editor from owner is the business of the
    individual route's own `_require_role`; this one only answers "does the
    caller hold any grant on this project at all".
    """
    resp = client.get("/api/v1/projects/p1/plans", headers=_hdr(roles=roles))
    assert resp.status_code == 200
    assert resp.json() == {"project_id": "p1"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/process/cycles",
        "/api/v1/memory/health",
    ],
)
def test_non_project_paths_are_not_gated(client: TestClient, path: str) -> None:
    """The self-limiting half.

    `/api/v1/process/*` is TENANT-scoped (its scope is the tenant header,
    not a project) and `/health` is open. Both live in routers that also
    serve project content, so a blanket gate would have broken them — which
    is why the predicate is the path and not the router.
    """
    resp = client.get(path, headers=_hdr(roles="user"))
    assert resp.status_code == 200


def test_retrieve_stays_reachable_with_global_roles_only(client: TestClient) -> None:
    """The specific regression this self-limiting prevents.

    C3 calls `/api/v1/memory/retrieve` on behalf of a chat user, forwarding
    the roles IT received — and its own request is `/api/v1/conversations/…`,
    so C2 appended no project role. Gating this path would have returned 403
    to every RAG query in the platform.
    """
    resp = client.post("/api/v1/memory/retrieve", headers=_hdr(roles="user"))
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# The path predicate itself
# ---------------------------------------------------------------------------


def test_a_bare_projects_collection_is_not_project_content(
    client: TestClient,
) -> None:
    """`/api/v1/projects` (no id) is a listing, not content.

    The regex requires `/projects/<id>/` with a TRAILING slash, so neither
    the collection nor the bare `/projects/{pid}` metadata resource is
    gated — matching the governance/content split the gateway uses. Asserted
    through the router rather than by reading the regex, so the two cannot
    drift.
    """
    app = FastAPI()
    router = APIRouter(dependencies=[Depends(require_project_content_role)])

    @router.get("/api/v1/projects")
    async def _list() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/api/v1/projects/{project_id}")
    async def _meta(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    app.include_router(router)
    c = TestClient(app)
    assert c.get("/api/v1/projects", headers=_hdr(roles="user")).status_code == 200
    assert c.get("/api/v1/projects/p1", headers=_hdr(roles="user")).status_code == 200
