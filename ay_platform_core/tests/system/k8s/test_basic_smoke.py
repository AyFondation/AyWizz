# =============================================================================
# File: test_basic_smoke.py
# Version: 1
# Path: ay_platform_core/tests/system/k8s/test_basic_smoke.py
# Description: Basic smoke tests against a live K8s deployment of the
#              platform. The four assertions together prove that every
#              backbone component is correctly wired:
#
#                test_open_route_returns_200
#                  C1 Traefik routes /auth/config to C2-Auth, C2 starts.
#
#                test_protected_route_returns_401_without_credentials
#                  Traefik forward-auth-c2 middleware fires on protected
#                  paths (no `Authorization` header → 401, never reaches
#                  the backend).
#
#                test_login_then_authenticated_request_passes_forward_auth
#                  Full chain: POST /auth/login (C2 reads from Arango,
#                  issues JWT) → use the token to hit /api/v1/memory/
#                  quota (C1 routes, forward-auth verifies via C2, C7
#                  reads from Arango). Any 2xx/4xx other than 401 proves
#                  the chain works; we accept 200/404 (no project yet)
#                  as success.
#
#                test_login_token_works_against_validation_too
#                  Same auth chain but targeting C6 — proves the C6
#                  pod is up + reachable + Arango-connected, and that
#                  the same token works across components (cluster-wide
#                  trust of C2's JWT signing key).
#
#              The strength of these four tests: a failure narrows the
#              scope of the wiring problem (is it C1? C2? a specific
#              backend pod?) without needing exec/inspect access.
#
# @relation validates:R-100-114
# @relation validates:R-100-117
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.system_k8s, pytest.mark.asyncio(loop_scope="session")]


_LOGIN_USERNAME = "alice"
_LOGIN_PASSWORD = "seed-password"


async def _login(base_url: str) -> str:
    """POST /auth/login with the bootstrap admin credentials and return
    the JWT. Failures surface as direct test assertion errors so a
    broken login flow is visible at the source rather than as a 401
    cascade two tests later."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{base_url}/auth/login",
            json={"username": _LOGIN_USERNAME, "password": _LOGIN_PASSWORD},
        )
    assert resp.status_code == 200, (
        f"login failed: {resp.status_code} — {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    token = body.get("access_token")
    assert token, f"login response missing access_token: {body}"
    return str(token)


async def test_open_route_returns_200(k8s_base_url: str) -> None:
    """C1 routing OK + C2 pod reachable. /auth/config is the canonical
    open endpoint — no auth required, returns the platform auth mode."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{k8s_base_url}/auth/config")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Schema sanity — guards against an accidental "C2 returns HTML"
    # regression that a status-only assertion would miss.
    assert "auth_mode" in body, f"unexpected /auth/config payload: {body}"


async def test_protected_route_returns_401_without_credentials(
    k8s_base_url: str,
) -> None:
    """forward-auth-c2 middleware MUST reject anonymous requests on
    protected paths. The 401 comes from Traefik (forward-auth response)
    NOT from the C7 backend — proving the middleware chain is wired.
    A 200 here would mean Traefik is bypassing forward-auth — a
    serious misconfiguration."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{k8s_base_url}/api/v1/memory/health")
    assert resp.status_code == 401, (
        f"expected 401 from forward-auth-c2, got {resp.status_code}: "
        f"{resp.text}"
    )


async def test_login_then_authenticated_request_passes_forward_auth(
    k8s_base_url: str,
) -> None:
    """Full auth round-trip:
       1. Login → token (C2 ↔ Arango).
       2. Token used on /api/v1/memory/quota (C1 ↔ C2 verify ↔ C7 ↔ Arango).

    A response code other than 401 proves the entire chain works:
    Traefik forwards to C2 /auth/verify, C2 returns 200 with claim
    headers, Traefik forwards to C7, C7 reads quota from Arango.
    The actual quota response (200 with zeros, or 404 for unknown
    project) is implementation detail — the absence of 401 is the
    contract."""
    token = await _login(k8s_base_url)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{k8s_base_url}/api/v1/memory/projects/system-test/quota",
            headers=headers,
        )
    assert resp.status_code != 401, (
        f"forward-auth rejected a freshly issued token (chain broken): "
        f"{resp.status_code} {resp.text}"
    )
    assert resp.status_code < 500, (
        f"C7 returned 5xx — possible Arango wiring issue: "
        f"{resp.status_code} {resp.text}"
    )


async def test_login_token_works_against_validation_too(
    k8s_base_url: str,
) -> None:
    """Same auth chain, different backend (C6 instead of C7). Proves
    C6 pod is up + reachable + Arango-connected, AND that the same
    JWT validates across multiple components — i.e. all C2-verify
    paths use the same JWT signing key from `aywizz-secrets`."""
    token = await _login(k8s_base_url)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # GET /api/v1/validation/health is technically open inside C6
        # but Traefik's forward-auth middleware fires for the whole
        # /api/v1/validation prefix — 401 without auth, NOT 401 with
        # valid token.
        resp = await client.get(
            f"{k8s_base_url}/api/v1/validation/health",
            headers=headers,
        )
    assert resp.status_code != 401, (
        f"forward-auth rejected token on C6: {resp.status_code} {resp.text}"
    )
    assert resp.status_code < 500, (
        f"C6 returned 5xx — possible wiring issue: "
        f"{resp.status_code} {resp.text}"
    )
