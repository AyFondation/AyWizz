# =============================================================================
# File: test_isolation.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/test_isolation.py
# Description: Cross-tenant + cross-project leak detection on
#              ITEM-LEVEL endpoints (paths that target a SPECIFIC
#              resource via id/slug). Item endpoints SHALL return
#              404/403 when the path's resource lives in a different
#              tenant or project than the caller.
#
#              List endpoints (GET .../{project_id}/things) are
#              EXCLUDED from this test because returning `200 []`
#              for an empty tenant view is correct — no data was
#              leaked, the requester just sees their own (empty)
#              collection. A leak there would manifest as items in
#              the response body that belong to a foreign tenant —
#              not as the status code. That dimension is covered by
#              `test_backend_state.py` after seeding.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from tests.e2e.auth_matrix._catalog import (
    Auth,
    EndpointSpec,
    Scope,
    endpoint_id,
    project_scoped,
    tenant_scoped,
)
from tests.e2e.auth_matrix._clients import (
    RoleProfile,
    build_bearer_headers,
    build_forward_auth_headers,
    make_asgi_client,
    needs_bearer,
)
from tests.e2e.auth_matrix._stack import PlatformStack

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _is_item_endpoint(spec: EndpointSpec) -> bool:
    """An item endpoint targets a SPECIFIC resource via a trailing
    `{resource_id}` segment. List endpoints (no terminal id, just
    `{project_id}` or no placeholder at all) are excluded — they
    legitimately return 200 [] for any tenant view."""
    item_terminals = ("_id}", "{slug}", "{version}")
    return any(spec.path.endswith(t) or t in spec.path.split("/")[-1] for t in item_terminals)


_TENANT_SCOPED = [e for e in tenant_scoped() if e.auth != Auth.OPEN and _is_item_endpoint(e)]
_PROJECT_SCOPED = [e for e in project_scoped() if e.auth != Auth.OPEN and _is_item_endpoint(e)]
_SUCCESS_CODES = {200, 201, 202, 204}


def _interpolate(path: str) -> str:
    out = path
    for placeholder in ("project_id", "user_id", "session_id", "conversation_id",
                        "run_id", "source_id", "entity_id", "slug", "version",
                        "job_id", "finding_id"):
        out = out.replace("{" + placeholder + "}", f"iso-{placeholder}")
    return out


async def _call(
    spec: EndpointSpec,
    stack: PlatformStack,
    profile: RoleProfile,
) -> httpx.Response:
    app = stack.app_for(spec.component)
    path = _interpolate(spec.path)
    body: dict[str, object] | None = None if spec.method == "GET" else {}
    if needs_bearer(spec):
        headers = await build_bearer_headers(stack.c2_service, profile)
    else:
        headers = build_forward_auth_headers(profile)
    async with make_asgi_client(app) as client:
        return await client.request(spec.method, path, headers=headers, json=body)


# ---------------------------------------------------------------------------
# (a) Cross-tenant: foreign tenant SHALL NOT receive 2xx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec",
    _TENANT_SCOPED + _PROJECT_SCOPED,
    ids=[endpoint_id(e) for e in _TENANT_SCOPED + _PROJECT_SCOPED],
)
async def test_cross_tenant_attempt_returns_no_data(
    spec: EndpointSpec,
    auth_matrix_stack: PlatformStack,
    profiles: dict[str, RoleProfile],
) -> None:
    """A user in tenant_b carrying the same role profile SHALL NOT
    receive a 2xx when targeting tenant_a's path. The X-Tenant-Id
    header carries tenant_b; the role check inside the handler may
    succeed (the user has the role) but the resource lookup MUST
    fail because the resource lives in tenant_a."""
    foreign = profiles["project_owner_other_tenant"]
    response = await _call(spec, auth_matrix_stack, foreign)
    assert response.status_code not in _SUCCESS_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} for "
        f"a cross-tenant request (X-Tenant-Id={foreign.tenant_id}, "
        f"path targets a different tenant's resource). This is a "
        f"data-leak bug. Body: {response.text[:300]}"
    )


# ---------------------------------------------------------------------------
# (b) Cross-project: same tenant, role on different project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec", _PROJECT_SCOPED, ids=[endpoint_id(e) for e in _PROJECT_SCOPED]
)
async def test_cross_project_attempt_returns_no_data(
    spec: EndpointSpec,
    auth_matrix_stack: PlatformStack,
    profiles: dict[str, RoleProfile],
) -> None:
    """A user holding `project_owner` on PROJECT_B SHALL NOT receive a
    2xx when targeting PROJECT_A's path within the same tenant. The
    role gate may pass (the user has SOME project role) but the
    resource SHALL NOT be exposed because the user's grant doesn't
    cover that project."""
    if spec.scope != Scope.PROJECT:
        pytest.skip(f"endpoint scope is {spec.scope.value}, not project")
    foreign = profiles["project_owner_other_project"]
    response = await _call(spec, auth_matrix_stack, foreign)
    assert response.status_code not in _SUCCESS_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} for "
        f"a cross-project request (user holds project_owner on "
        f"{foreign.project_id}, path targets a different project). "
        f"This is a data-leak bug. Body: {response.text[:300]}"
    )
