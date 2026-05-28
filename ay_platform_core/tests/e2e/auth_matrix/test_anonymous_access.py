# =============================================================================
# File: test_anonymous_access.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/test_anonymous_access.py
# Description: Auto-parametrised over `_catalog.ENDPOINTS`. For every
#              non-OPEN endpoint, verifies that a request carrying NO
#              identity (no Bearer JWT, no forward-auth headers) MUST
#              NOT return a success status.
#
#              We deliberately accept any of {401, 403, 404, 422} as
#              "request rejected": some endpoints reject via auth dep
#              (401), some via role dep (403), some via 404-on-missing-
#              tenant, and some via body validation (422) when the
#              body would have to be parsed BEFORE the auth dep runs.
#              The hard guarantee is that anonymous access SHALL NEVER
#              return a 2xx — that's what we assert.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import pytest

from tests.e2e.auth_matrix._catalog import (
    ENDPOINTS,
    Auth,
    EndpointSpec,
    endpoint_id,
)
from tests.e2e.auth_matrix._clients import build_anonymous_headers, make_asgi_client
from tests.e2e.auth_matrix._stack import PlatformStack

# `loop_scope="session"` must match the auth_matrix_stack fixture's
# loop_scope (see conftest.py) — otherwise pytest-asyncio builds the
# fixture in a function-scoped loop that closes before later tests run,
# causing silent hangs on cumulative test runs.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


_PROTECTED = [e for e in ENDPOINTS if e.auth != Auth.OPEN]
_SUCCESS_CODES = {200, 201, 202, 204}


def _interpolate(path: str) -> str:
    """Replace `{placeholder}` segments with safe dummies. Anonymous
    requests should never reach a handler so the value is irrelevant."""
    out = path
    for placeholder in ("project_id", "user_id", "session_id", "conversation_id",
                        "run_id", "source_id", "entity_id", "slug", "version",
                        "job_id", "finding_id"):
        out = out.replace("{" + placeholder + "}", f"anon-{placeholder}")
    return out


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec", _PROTECTED, ids=[endpoint_id(e) for e in _PROTECTED]
)
async def test_anonymous_request_is_rejected(
    spec: EndpointSpec, auth_matrix_stack: PlatformStack
) -> None:
    """No identity → no success. Auth dep, role dep, or 404 on missing
    tenant SHALL fire before any 2xx is returned."""
    app = auth_matrix_stack.app_for(spec.component)
    path = _interpolate(spec.path)
    headers = build_anonymous_headers()
    body: dict[str, object] | None = None if spec.method == "GET" else {}

    async with make_asgi_client(app) as client:
        response = await client.request(spec.method, path, headers=headers, json=body)

    assert response.status_code not in _SUCCESS_CODES, (
        f"{spec.method} {spec.path} returned {response.status_code} for "
        f"anonymous caller; expected an authentication / authorization "
        f"failure code (401/403/404) or a body-validation rejection "
        f"(422). Body: {response.text[:300]}"
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "spec",
    [e for e in ENDPOINTS if e.auth == Auth.OPEN],
    ids=[endpoint_id(e) for e in ENDPOINTS if e.auth == Auth.OPEN],
)
async def test_open_endpoint_succeeds_without_auth(
    spec: EndpointSpec, auth_matrix_stack: PlatformStack
) -> None:
    """OPEN endpoints (health, login, config) SHALL be reachable without
    any auth — we assert the response is NOT a 401/403, since the
    catalog claims they're open."""
    app = auth_matrix_stack.app_for(spec.component)
    path = _interpolate(spec.path)
    body: dict[str, object] | None = None if spec.method == "GET" else {}

    async with make_asgi_client(app) as client:
        response = await client.request(spec.method, path, json=body)

    assert response.status_code not in (401, 403), (
        f"OPEN endpoint {spec.method} {spec.path} returned "
        f"{response.status_code} (auth-required); the catalog claims "
        f"this endpoint is open. Body: {response.text[:300]}"
    )
