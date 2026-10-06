# =============================================================================
# File: test_isolation.py
# Version: 2
# Path: ay_platform_core/tests/e2e/auth_matrix/test_isolation.py
# Description: Cross-tenant + cross-project leak detection on
#              ITEM-LEVEL endpoints (paths that target a SPECIFIC
#              resource via id/slug). Item endpoints SHALL return
#              404/403 when the path's resource lives in a different
#              tenant or project than the caller.
#
#              List endpoints (GET .../{project_id}/things) are
#              EXCLUDED, because a `200 []` for an empty view is
#              correct and this test asserts on the STATUS. A leak
#              there would show up as foreign items in the BODY.
#
#              THE PREVIOUS VERSION OF THIS PARAGRAPH CLAIMED that
#              dimension was "covered by `test_backend_state.py`
#              after seeding". It is not: that file holds eight
#              write-persistence tests and not one of them makes a
#              cross-project or cross-tenant assertion. An exclusion
#              justified by a cross-reference to coverage that does
#              not exist is worse than an unjustified one — it stops
#              anybody looking. The 47 genuine collection endpoints
#              therefore have NO cross-project leak test today; the
#              gap is enumerated in
#              `tests/coherence/test_project_role_gate_ratchet.py`
#              instead of being asserted away here, so it is at
#              least visible and cannot grow unnoticed.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import re

import httpx
import pytest

from tests.e2e.auth_matrix._catalog import (
    Auth,
    EndpointSpec,
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


#: Placeholders that identify the SCOPE of a request rather than a resource
#: within it. A path carrying only these addresses a collection.
_SCOPE_PLACEHOLDERS = frozenset({"project_id", "tenant_id"})

_PLACEHOLDER_NAMES_RE = re.compile(r"\{([^}]+)\}")


def _is_item_endpoint(spec: EndpointSpec) -> bool:
    """True when the path targets a SPECIFIC resource inside the scope.

    The test is simply "does the path carry a placeholder other than
    `{project_id}` / `{tenant_id}`" — if it names a resource, it is an item
    endpoint, wherever that placeholder sits in the path.

    **WHY THIS REPLACED A TERMINAL-SUFFIX MATCH.** The previous version
    matched only a TRAILING `{..._id}` / `{slug}` / `{version}`, so any
    sub-resource suffix defeated it and the endpoint was silently reclassified
    as a "list". An audit on 2026-10-06 measured the damage: of 136
    project-scoped non-open endpoints, 109 were excluded as lists — but only
    47 of those were genuine collections. The other **62 were item endpoints
    misclassified**, among them ten GETs that stream another project's bytes
    or archives:

        /api/v1/projects/{project_id}/backups/{backup_id}/download
        /api/v1/memory/projects/{project_id}/sources/{source_id}/blob
        /api/v1/memory/projects/{project_id}/sources/{source_id}/chunks.zip
        /api/v1/projects/{project_id}/artifacts/runs/{run_id}/blob
        /api/v1/projects/{project_id}/baselines/{tag}/render/{fmt}
        …

    `{fmt}`, `{tag}`, `{container}`, `{path:path}` were not in the terminal
    list at all, and `…/{object_id}/versions`, `…/{entity_id}/history`,
    `…/{source_id}/diagnostics` ended on a literal segment. Cross-project
    isolation on all of them was reported as "not applicable" rather than
    "untested", which is the worst way for a gap to present itself.

    Isolation on those paths does currently hold — the storage keys are
    compound (`tenant:project:resource`) and forward-auth derives the
    caller's role from the project IN THE PATH — so this was a latent
    regression risk, not a live breach. The point of the test is to keep it
    that way.
    """
    names = set(_PLACEHOLDER_NAMES_RE.findall(spec.path))
    # `{path:path}` and friends carry a converter suffix.
    bare = {n.split(":", 1)[0] for n in names}
    return bool(bare - _SCOPE_PLACEHOLDERS)


_TENANT_SCOPED = [e for e in tenant_scoped() if e.auth != Auth.OPEN and _is_item_endpoint(e)]
_PROJECT_SCOPED = [e for e in project_scoped() if e.auth != Auth.OPEN and _is_item_endpoint(e)]
_SUCCESS_CODES = {200, 201, 202, 204}


def _interpolate(path: str) -> str:
    """Replace EVERY placeholder with a deterministic foreign-looking value.

    Generic on purpose. This used to carry a hardcoded list of eleven
    placeholder names, which was a false-pass generator: a path containing a
    name absent from the list (`{container}`, `{tag}`, `{fmt}`,
    `{path:path}`, `{object_id}`, `{drop_id}`, …) kept its literal braces,
    matched no route, and returned 404 — satisfying `status not in 2xx` for
    a reason that has nothing to do with authorization. The test would have
    reported "no leak" on an endpoint it never actually reached. Widening
    `_is_item_endpoint` brought 62 such paths into the parametrisation at
    once, so the list approach had to go.
    """
    def _value(match: re.Match[str]) -> str:
        name = match.group(1).split(":", 1)[0]
        return f"iso-{name}"

    return _PLACEHOLDER_NAMES_RE.sub(_value, path)


def _path_aware_forward_auth_headers(
    profile: RoleProfile, concrete_path: str
) -> dict[str, str]:
    """Forward-auth headers as C2 would really derive them FOR THIS PATH.

    **WHY THIS OVERRIDES THE SHARED HELPER.** `build_forward_auth_headers`
    emits `profile.x_user_roles` verbatim, with no reference to the request
    path. That is faithful for every other dimension of the matrix, and
    unfaithful for exactly this one: C2 appends a caller's project role only
    when the forwarded URI matches `…/projects/{pid}/…` AND the caller holds
    a scope on THAT project (`_forward_auth_roles` in `c2_auth.router`). So
    the shared helper hands a foreign-project caller their own
    `project_owner` on somebody else's path — it fabricates the very
    derivation that enforces cross-project isolation, and then the test
    claims to have verified it.

    The consequence was not theoretical. Every cross-project case in this
    file passed because the fabricated resource id produced a 404, never
    because a gate refused; and endpoints that tolerate an empty result
    (a `204` idempotent delete, a `200 []` listing) failed with a leak
    message for an endpoint that is in fact correctly gated.

    Here the project role is appended only when the path's project matches
    the profile's. On an `iso-project_id` path nobody holds a scope, so a
    role-gated endpoint answers 403 — the right answer for the right
    reason — and an endpoint with NO role gate answers 2xx and fails this
    test, which is also correct: it has none.
    """
    headers = build_forward_auth_headers(profile)
    if not profile.project_id or not profile.project_role:
        return headers
    if f"/projects/{profile.project_id}" not in concrete_path:
        # C2 would find no scope for the path's project → global roles only.
        globals_only = [
            r
            for r in headers["X-User-Roles"].split(",")
            if r.strip() and r.strip() != profile.project_role
        ]
        headers["X-User-Roles"] = ",".join(globals_only)
    return headers


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
        headers = _path_aware_forward_auth_headers(profile, path)
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
    cover that project.

    There is deliberately NO scope guard here: `_PROJECT_SCOPED` is already
    filtered through `project_scoped()`, so an in-body
    `if spec.scope != Scope.PROJECT: pytest.skip(...)` was unreachable dead
    code — and misleading, because it implied non-project endpoints were
    being skipped when they were never parametrised at all.
    """
    foreign = profiles["project_owner_other_project"]
    response = await _call(spec, auth_matrix_stack, foreign)
    assert response.status_code not in _SUCCESS_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} for "
        f"a cross-project request (user holds project_owner on "
        f"{foreign.project_id}, path targets a different project). "
        f"This is a data-leak bug. Body: {response.text[:300]}"
    )
