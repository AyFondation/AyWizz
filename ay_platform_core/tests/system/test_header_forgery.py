# =============================================================================
# File: test_header_forgery.py
# Version: 1
# Path: ay_platform_core/tests/system/test_header_forgery.py
# Description: Proves the gateway overwrites the forward-auth identity
#              headers rather than passing a client's own values through.
#
#              WHY THIS EXISTS. Every backend in the platform gates on
#              `X-User-Id` / `X-User-Roles` / `X-Tenant-Id`. Those headers
#              are ordinary HTTP headers: a caller can set them. The ONLY
#              thing that makes the whole authorization model sound is that
#              Traefik's forwardAuth replaces them with the values C2 derived
#              from the verified JWT, for every header named in
#              `authResponseHeaders`. If that replacement ever stopped
#              happening — a renamed header, a middleware dropped from a
#              router, an `authResponseHeaders` list that drifts from what
#              the backends read — then `X-User-Roles: platform_manager`
#              typed by hand would be a complete privilege escalation, and
#              NOTHING else in the suite would notice.
#
#              It cannot be tested below the system tier: the unit, contract,
#              integration and e2e tiers all inject these headers themselves
#              (that is how they simulate forward-auth), so they assume the
#              property this test verifies. Only a request through the real
#              Traefik can tell.
#
#              NO `@relation validates:` MARKER, deliberately — same reason
#              as the other two guards of this kind: this protects a defect
#              class, not one requirement, and a false claim would make
#              `060-IMPLEMENTATION-STATUS.md` report a requirement `tested`
#              on the strength of a comment.
# =============================================================================

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.system

#: A surface restricted to `platform_manager`. The seeded `alice` is
#: `admin` + `project_owner` on `demo`, so she is legitimately refused —
#: which is what makes a 200 here unambiguous evidence of forgery working.
_PLATFORM_MANAGER_ONLY = "/admin/v1/llm/registry"


@pytest.mark.asyncio
async def test_forged_user_roles_header_does_not_grant_the_role(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A client-supplied `X-User-Roles` SHALL NOT reach the backend.

    The caller holds a valid token, so forward-auth succeeds and the request
    is forwarded — the question is purely whose role list arrives. Traefik
    must overwrite the header with C2's derivation.
    """
    honest = await gateway_client.get(
        _PLATFORM_MANAGER_ONLY, headers=auth_headers
    )
    assert honest.status_code == 403, (
        "precondition broken: the seeded user is expected to LACK "
        f"platform_manager on {_PLATFORM_MANAGER_ONLY}, but got "
        f"{honest.status_code}. Without that, this test proves nothing."
    )

    forged = await gateway_client.get(
        _PLATFORM_MANAGER_ONLY,
        headers={**auth_headers, "X-User-Roles": "platform_manager"},
    )
    assert forged.status_code == 403, (
        "PRIVILEGE ESCALATION: sending `X-User-Roles: platform_manager` "
        f"turned a 403 into {forged.status_code}. The gateway is passing a "
        "client-supplied role list through to the backend. Check that "
        "`X-User-Roles` is still listed in `authResponseHeaders` on the "
        "`forward-auth-c2` middleware, and that this route carries that "
        f"middleware at all. Body: {forged.text[:200]}"
    )


@pytest.mark.asyncio
async def test_forged_identity_without_a_token_is_refused_at_the_gateway(
    gateway_client: httpx.AsyncClient,
) -> None:
    """Identity headers alone SHALL NOT authenticate anything.

    No bearer token: forward-auth must fail and the backend must never be
    reached, no matter how complete the forged header set looks.
    """
    resp = await gateway_client.get(
        _PLATFORM_MANAGER_ONLY,
        headers={
            "X-User-Id": "attacker",
            "X-Tenant-Id": "default",
            "X-User-Roles": "platform_manager,admin,tenant_admin",
        },
    )
    assert resp.status_code == 401, (
        "a request with no credentials but a complete set of forged "
        f"forward-auth headers got {resp.status_code}, expected 401. Either "
        "the route lost its forward-auth middleware or the backend accepted "
        f"the forged headers directly. Body: {resp.text[:200]}"
    )


@pytest.mark.asyncio
async def test_forged_project_scopes_header_does_not_grant_a_project_role(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A client-supplied `X-Project-Scopes` SHALL NOT grant anything.

    This header (inc3b) carries the caller's whole project→roles map so
    components whose project id is not in the URI — C9/MCP above all — can
    resolve the caller's role on the project they were asked about. That
    makes it a role grant in wire form, and it is safe for exactly one
    reason: it is listed in the `forward-auth-c2` middleware's
    `authResponseHeaders`, so Traefik replaces whatever the caller sent
    with C2's derivation from the verified JWT.

    The probe claims owner on a project the seeded user has no grant on,
    through the MCP tool that consumes the header. A 202 would mean a
    self-service write grant on any project in the tenant.
    """
    forged = await gateway_client.post(
        "/api/v1/mcp",
        headers={
            **auth_headers,
            "X-Project-Scopes": "victim-project=project_owner,project_editor",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "c6_trigger_validation",
                "arguments": {
                    "domain": "code",
                    "project_id": "victim-project",
                    "check_ids": ["interface-signature-drift"],
                },
            },
        },
    )
    assert forged.status_code == 200, forged.text
    body = forged.json()
    result = body.get("result", {})
    assert result.get("isError") is True, (
        "PRIVILEGE ESCALATION: a forged `X-Project-Scopes` was honoured and "
        "the run was accepted on a project the caller holds no grant on. "
        "Check that `X-Project-Scopes` is still in `authResponseHeaders` on "
        f"the forward-auth-c2 middleware. Response: {body}"
    )
    text = " ".join(
        block.get("text", "") for block in result.get("content", [])
    )
    assert "403" in text, (
        "the call failed, but not with the authorization refusal this test "
        f"is about — so it proves nothing about the header. Got: {text!r}"
    )


@pytest.mark.asyncio
async def test_forged_tenant_header_does_not_change_the_caller_tenant(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A forged `X-Tenant-Id` SHALL NOT move the caller to another tenant.

    Tenant isolation rests on this header, so a caller who could set it
    would read across tenants. The assertion is on the DATA, not the status:
    a 200 is expected here — what matters is that the projects returned
    belong to the caller's real tenant, not the one they asked for.
    """
    resp = await gateway_client.get(
        "/api/v1/projects",
        headers={**auth_headers, "X-Tenant-Id": "forged-tenant"},
    )
    assert resp.status_code == 200, resp.text
    tenants = {item["tenant_id"] for item in resp.json()["items"]}
    assert "forged-tenant" not in tenants, (
        "TENANT ISOLATION BREACH: a forged `X-Tenant-Id` was honoured and "
        f"returned data for it. Tenants in the response: {sorted(tenants)}"
    )
    assert tenants, (
        "the response carried no projects at all, so this test could not "
        "observe which tenant was used. Check the seeder."
    )
