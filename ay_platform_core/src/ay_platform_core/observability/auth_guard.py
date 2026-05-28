# =============================================================================
# File: auth_guard.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/auth_guard.py
# Description: Defense-in-depth authentication guard for backend
#              components. Middleware that returns 401 immediately if a
#              request reaches a protected path WITHOUT the
#              `X-User-Id` forward-auth header.
#
#              Two-layer auth scheme:
#                Layer 1 (edge) — C1 Traefik calls C2 /auth/verify on
#                  every protected route; missing/invalid JWT → 401
#                  before the backend pod is ever contacted.
#                Layer 2 (component, this middleware) — if a request
#                  somehow reaches the backend without the forward-auth
#                  headers (Traefik misconfig, direct in-cluster call
#                  bypassing Traefik, malicious pod), 401 immediately
#                  rather than trust empty claims.
#
#              Per-component authorization (role / tenant / project
#              membership) STILL lives in the route handlers — this
#              middleware is binary "is there an authenticated user
#              at all?" not fine-grained rights.
#
#              Components opt in by adding the middleware in their
#              `create_app()`:
#                  app.add_middleware(
#                      AuthGuardMiddleware,
#                      component="c3_conversation",
#                  )
#              Default exempt prefixes: `/health` (K8s probe), `/metrics`
#              if exposed. C2 adds `/auth/config`, `/auth/login`,
#              `/auth/token` to the exempt list — those are the only
#              endpoints called WITHOUT a JWT (the public auth
#              surface).
#
# @relation implements:R-100-039
# @relation implements:R-100-118
# =============================================================================

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_log = logging.getLogger("ay.observability.auth_guard")

# Default paths that bypass the auth check.
# `/health` — K8s liveness/readiness probes (kubelet has no JWT).
# `/metrics` — placeholder for a future Prometheus surface that scrapers
# would also hit unauthenticated, scraped only from inside the cluster.
_DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = ("/health", "/metrics")


class AuthGuardMiddleware:
    """ASGI middleware that 401's any request to a non-exempt path
    when the `X-User-Id` forward-auth header is missing or empty."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        component: str = "",
        exempt_prefixes: Iterable[str] | None = None,
    ) -> None:
        self._app = app
        self._component = component or "unknown"
        self._exempt: tuple[str, ...] = (
            tuple(exempt_prefixes)
            if exempt_prefixes is not None
            else _DEFAULT_EXEMPT_PREFIXES
        )

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in self._exempt):
            await self._app(scope, receive, send)
            return

        x_user_id = _find_header(scope, b"x-user-id")
        if x_user_id:
            await self._app(scope, receive, send)
            return

        # Reject — request bypassed the edge auth somehow. Audit-log
        # so operators see deflected internal-bypass attempts; these
        # are noteworthy in a healthy cluster.
        method = scope.get("method", "?")
        _log.warning(
            "auth_guard_block",
            extra={
                "event": "auth_guard_block",
                "component": self._component,
                "path": path,
                "method": method,
            },
        )
        await _send_401(scope, receive, send, component=self._component)


async def _send_401(
    scope: Scope, receive: Receive, send: Send, *, component: str,
) -> None:
    """Emit a 401 response without invoking the wrapped app. Body shape
    matches FastAPI's HTTPException(401) so clients can parse it the
    same way."""
    body_obj: dict[str, Any] = {
        "detail": "missing forward-auth identity",
        "component": component,
    }
    body = json.dumps(body_obj).encode("utf-8")
    response_start: Message = {
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    }
    response_body: Message = {
        "type": "http.response.body",
        "body": body,
        "more_body": False,
    }
    await send(response_start)
    await send(response_body)
    # `receive` is intentionally NOT consumed; for short-circuited
    # responses this is fine, the request body is discarded by ASGI.
    del scope, receive


def _find_header(scope: Scope, name: bytes) -> str | None:
    """Lookup a request header by lowercase name. Returns None if the
    header is absent or has an empty value."""
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name == name:
            value = raw_value.decode("latin-1").strip()
            return value or None
    return None
