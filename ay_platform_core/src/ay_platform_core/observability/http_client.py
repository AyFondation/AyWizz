# =============================================================================
# File: http_client.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/http_client.py
# Description: Factory for `httpx.AsyncClient` instances that automatically
#              inject the current `traceparent` on every outgoing request.
#              Components SHALL use this factory rather than constructing
#              `httpx.AsyncClient(...)` directly so cross-component
#              traffic never loses the trace.
#
# @relation implements:R-100-105
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx

from ay_platform_core.observability.context import (
    current_tenant_id,
    current_traceparent,
    current_user_id,
    current_user_roles,
)


async def _inject_request_context(request: httpx.Request) -> None:
    """httpx event hook: stamp the current trace + auth context on the
    outgoing request.

    Injects (when the corresponding ContextVar is set):
      - `traceparent` (W3C Trace Context — R-100-105)
      - `X-User-Id`, `X-User-Roles`, `X-Tenant-Id` (forward-auth bundle —
        R-100-118). Required so a fan-out call (C9 → C5/C6) carries the
        same identity that Traefik forward-auth originally injected;
        without it the downstream's guard returns 401.

    Each header uses `setdefault` so an explicit caller-provided value
    wins (rare, but supported — e.g. relays forwarding a different
    user's identity).
    """
    tp = current_traceparent()
    if tp:
        request.headers.setdefault("traceparent", tp)
    user_id = current_user_id()
    if user_id:
        request.headers.setdefault("X-User-Id", user_id)
    user_roles = current_user_roles()
    if user_roles:
        request.headers.setdefault("X-User-Roles", user_roles)
    tenant_id = current_tenant_id()
    if tenant_id:
        request.headers.setdefault("X-Tenant-Id", tenant_id)


def make_traced_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
    """Build a `httpx.AsyncClient` with auto trace + auth context propagation.

    Forwards every argument to `httpx.AsyncClient(...)`. If the caller
    already declared `event_hooks`, the context hook is appended to its
    `request` list (no override).
    """
    event_hooks: dict[str, list[Any]] = kwargs.pop("event_hooks", {}) or {}
    request_hooks = list(event_hooks.get("request", []))
    request_hooks.append(_inject_request_context)
    event_hooks = {**event_hooks, "request": request_hooks}
    return httpx.AsyncClient(*args, event_hooks=event_hooks, **kwargs)
