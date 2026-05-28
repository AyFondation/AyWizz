# =============================================================================
# File: test_role_matrix.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/test_role_matrix.py
# Description: Auto-parametrised over ROLE_GATED endpoints in `_catalog`.
#              For every such endpoint, three assertions run:
#
#              (a) Insufficient role (`user` baseline) → response code
#                  MUST NOT be a success (2xx). Body validation may
#                  still fire first (422) — that also proves no data
#                  was returned.
#              (b) Accepted role (first of accept_roles or
#                  accept_global_roles) → response code MUST NOT be
#                  401/403. The role gate cleared; downstream errors
#                  (422 body, 500 service, etc.) are OK because they
#                  prove the request reached past the gate.
#              (c) Excluded global role (e.g. `tenant_manager` on
#                  content endpoints per E-100-002 v2 separation of
#                  duties) → response code MUST NOT be a success.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from tests.e2e.auth_matrix._catalog import (
    EndpointSpec,
    endpoint_id,
    role_gated,
)
from tests.e2e.auth_matrix._clients import (
    RoleProfile,
    build_bearer_headers,
    build_forward_auth_headers,
    make_asgi_client,
    needs_bearer,
)
from tests.e2e.auth_matrix._stack import PlatformStack

# loop_scope="session" matches the auth_matrix_stack fixture (see conftest).
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


_ROLE_GATED = role_gated()
_SUCCESS_CODES = {200, 201, 202, 204}
_ROLE_GATE_FAILURE_CODES = {401, 403}


def _interpolate(path: str) -> str:
    out = path
    for placeholder in ("project_id", "user_id", "session_id", "conversation_id",
                        "run_id", "source_id", "entity_id", "slug", "version",
                        "job_id", "finding_id"):
        out = out.replace("{" + placeholder + "}", f"role-{placeholder}")
    return out


async def _call(
    spec: EndpointSpec,
    stack: PlatformStack,
    profile: RoleProfile,
) -> httpx.Response:
    """Execute the endpoint with the appropriate auth headers for the
    target component (Bearer JWT for C2 admin paths, forward-auth
    headers everywhere else)."""
    app = stack.app_for(spec.component)
    path = _interpolate(spec.path)
    body: dict[str, object] | None = None if spec.method == "GET" else {}

    if needs_bearer(spec):
        headers = await build_bearer_headers(stack.c2_service, profile)
    else:
        headers = build_forward_auth_headers(profile)

    async with make_asgi_client(app) as client:
        return await client.request(spec.method, path, headers=headers, json=body)


def _first_accepted_profile(spec: EndpointSpec, profiles: dict[str, RoleProfile]) -> RoleProfile:
    """Pick a profile that holds at least one of the endpoint's accepted roles."""
    for global_role in spec.accept_global_roles:
        if global_role in profiles:
            return profiles[global_role]
    for project_role in spec.accept_roles:
        if project_role in profiles:
            return profiles[project_role]
    raise RuntimeError(
        f"no profile in fixture covers accepted roles for {spec.method} {spec.path}: "
        f"global={spec.accept_global_roles} project={spec.accept_roles}"
    )


# ---------------------------------------------------------------------------
# (a) Insufficient role — `user` baseline SHALL be rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec", _ROLE_GATED, ids=[endpoint_id(e) for e in _ROLE_GATED]
)
async def test_insufficient_role_is_rejected(
    spec: EndpointSpec,
    auth_matrix_stack: PlatformStack,
    profiles: dict[str, RoleProfile],
) -> None:
    """A user without any of the endpoint's accepted roles SHALL NOT
    receive a 2xx. We accept 401/403/404/422 — the guarantee is no
    data returned."""
    response = await _call(spec, auth_matrix_stack, profiles["user"])
    assert response.status_code not in _SUCCESS_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} "
        f"for `user` (no role grant); accepted roles are "
        f"global={spec.accept_global_roles} project={spec.accept_roles}. "
        f"Body: {response.text[:300]}"
    )


# ---------------------------------------------------------------------------
# (b) Accepted role — gate cleared, no 401/403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec", _ROLE_GATED, ids=[endpoint_id(e) for e in _ROLE_GATED]
)
async def test_accepted_role_clears_gate(
    spec: EndpointSpec,
    auth_matrix_stack: PlatformStack,
    profiles: dict[str, RoleProfile],
) -> None:
    """A user holding an accepted role SHALL clear the role gate.
    Downstream errors (422 body, 500 service, etc.) are NOT failures —
    they mean we reached past the auth/role layer."""
    profile = _first_accepted_profile(spec, profiles)
    response = await _call(spec, auth_matrix_stack, profile)
    assert response.status_code not in _ROLE_GATE_FAILURE_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} for "
        f"profile `{profile.role_label}` (which holds an accepted role); "
        f"the role gate should have cleared. Body: {response.text[:300]}"
    )


# ---------------------------------------------------------------------------
# (c) Excluded global role — content-blindness for tenant_manager
# ---------------------------------------------------------------------------


_EXCLUDED_TENANT_MANAGER = [
    e for e in _ROLE_GATED if "tenant_manager" in e.excluded_global_roles
]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec",
    _EXCLUDED_TENANT_MANAGER,
    ids=[endpoint_id(e) for e in _EXCLUDED_TENANT_MANAGER],
)
async def test_tenant_manager_excluded_from_content(
    spec: EndpointSpec,
    auth_matrix_stack: PlatformStack,
    profiles: dict[str, RoleProfile],
) -> None:
    """Per E-100-002 v2: `tenant_manager` SHALL be content-blind. On
    every endpoint that operates on tenant content, a request bearing
    only `tenant_manager` SHALL NOT receive a 2xx response."""
    response = await _call(spec, auth_matrix_stack, profiles["tenant_manager"])
    assert response.status_code not in _SUCCESS_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} for "
        f"`tenant_manager` — but this endpoint declares `tenant_manager` "
        f"as excluded (E-100-002 v2). The role gate must reject "
        f"tenant_manager on content endpoints. Body: {response.text[:300]}"
    )
