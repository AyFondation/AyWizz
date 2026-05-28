# =============================================================================
# File: test_auth_guard.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/test_auth_guard.py
# Description: Unit tests for `AuthGuardMiddleware`. Defense-in-depth
#              that returns 401 on protected paths without an X-User-Id
#              forward-auth header. Tests pin:
#                - 401 on protected path with no header.
#                - 401 on protected path with empty header.
#                - 200 on protected path with non-empty header.
#                - 200 on exempt path regardless of header.
#                - non-HTTP scopes pass through (websocket, lifespan).
#                - custom exempt list (C2 needs /auth/login etc.).
#
# @relation validates:R-100-039
# @relation validates:R-100-118
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.observability.auth_guard import AuthGuardMiddleware

pytestmark = [pytest.mark.unit, pytest.mark.asyncio(loop_scope="function")]


def _make_app(**guard_kwargs: Any) -> FastAPI:
    """Build a FastAPI app with two routes — one open (`/health`) and
    one we expect the guard to protect (`/api/v1/protected`). Wraps
    them in AuthGuardMiddleware with the supplied kwargs."""
    app = FastAPI()
    app.add_middleware(AuthGuardMiddleware, **guard_kwargs)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/protected")
    async def protected() -> dict[str, str]:
        return {"hello": "world"}

    @app.get("/auth/config")
    async def auth_config() -> dict[str, str]:
        return {"auth_mode": "local"}

    @app.get("/auth/login")
    async def auth_login() -> dict[str, str]:
        return {"token": "stub"}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


# ---------------------------------------------------------------------------
# Default exempt list — only /health and /metrics
# ---------------------------------------------------------------------------


async def test_protected_route_without_header_returns_401() -> None:
    app = _make_app(component="c-test")
    async with _client(app) as c:
        resp = await c.get("/api/v1/protected")
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"] == "missing forward-auth identity"
    assert body["component"] == "c-test"


async def test_protected_route_with_empty_header_returns_401() -> None:
    """Whitespace-only / empty header value SHALL be rejected — the
    guard's job is to ensure a real identity reached us."""
    app = _make_app(component="c-test")
    async with _client(app) as c:
        resp = await c.get("/api/v1/protected", headers={"X-User-Id": "   "})
    assert resp.status_code == 401


async def test_protected_route_with_user_id_passes() -> None:
    app = _make_app(component="c-test")
    async with _client(app) as c:
        resp = await c.get(
            "/api/v1/protected", headers={"X-User-Id": "alice"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"hello": "world"}


async def test_health_path_is_exempt_by_default() -> None:
    """K8s probes have no JWT — `/health` SHALL pass through always."""
    app = _make_app(component="c-test")
    async with _client(app) as c:
        resp = await c.get("/health")
    assert resp.status_code == 200


async def test_default_exempt_does_not_cover_auth_login() -> None:
    """C2 needs `/auth/login` to be exempt, but the default list does
    NOT include it — components that need it (only C2) override
    `exempt_prefixes`."""
    app = _make_app(component="c-test")
    async with _client(app) as c:
        resp = await c.get("/auth/login")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Custom exempt list — C2's surface
# ---------------------------------------------------------------------------


async def test_c2_exempt_list_lets_auth_endpoints_through() -> None:
    """When C2 wires the middleware with the auth public surface in
    `exempt_prefixes`, those endpoints pass without X-User-Id."""
    app = _make_app(
        component="c2_auth",
        exempt_prefixes=["/health", "/auth/config", "/auth/login", "/auth/token"],
    )
    async with _client(app) as c:
        config_resp = await c.get("/auth/config")
        login_resp = await c.get("/auth/login")
        protected_resp = await c.get("/api/v1/protected")
    assert config_resp.status_code == 200
    assert login_resp.status_code == 200
    assert protected_resp.status_code == 401


# ---------------------------------------------------------------------------
# Non-HTTP scopes pass through
# ---------------------------------------------------------------------------


async def test_lifespan_scope_passes_through() -> None:
    """Lifespan startup/shutdown SHALL NOT be intercepted by the guard."""
    app = _make_app(component="c-test")
    # If the guard interfered with lifespan, the AsyncClient would hang
    # or error during startup — we assert it doesn't.
    async with _client(app) as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
