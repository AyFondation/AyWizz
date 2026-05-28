# =============================================================================
# File: middleware.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/observability/middleware.py
# Description: ASGI middleware that joins or starts a W3C trace for every
#              inbound request. Strategy:
#                1. Try to parse the inbound `traceparent` header.
#                2. If valid, generate a NEW span_id (reusing the
#                   trace_id) and remember the inbound span_id as the
#                   `parent_span_id` — we are a child of the upstream
#                   span.
#                3. If absent / malformed, generate a fresh trace.
#              The resulting (trace_id, span_id, parent_span_id) tuple
#              is set on ContextVars for the request's lifetime, and a
#              `traceparent` response header is emitted so observers of
#              the outbound side can correlate.
#
#              v2 — phase-2 workflow synthesis: the middleware now emits
#              a structured `span_summary` log line at the end of every
#              request. Schema:
#                event=span_summary
#                method, path, status_code, duration_ms,
#                parent_span_id (inherited from inbound traceparent)
#              Combined with the trace_id / span_id automatically added
#              by the JSON formatter, these lines are the building
#              blocks the `_observability` aggregator (phase 3) will
#              group by trace_id to reconstruct the workflow tree.
#
#              Sampling: trace context inherits the inbound `sampled`
#              flag verbatim. Fresh traces use the configured
#              `TRACE_SAMPLE_RATE` (R-100-105).
#
# @relation implements:R-100-104
# @relation implements:R-100-105
# =============================================================================

from __future__ import annotations

import logging
import random
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ay_platform_core.observability.context import (
    TraceContext,
    build_traceparent,
    new_trace_context,
    parse_traceparent,
    set_auth_context,
    set_trace_context,
)

_log = logging.getLogger("ay.observability.middleware")


class TraceContextMiddleware:
    """ASGI middleware: parse / generate W3C `traceparent` and emit a
    `span_summary` log line at request end."""

    def __init__(self, app: ASGIApp, *, sample_rate: float = 1.0) -> None:
        self._app = app
        self._sample_rate = max(0.0, min(1.0, sample_rate))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Parse inbound traceparent. The inbound span_id (when present)
        # becomes our parent_span_id.
        inbound_value = _find_header(scope, b"traceparent")
        inbound = parse_traceparent(inbound_value)
        if inbound is None:
            new = new_trace_context(sampled=self._roll_sampled())
            ctx = TraceContext(
                trace_id=new.trace_id,
                span_id=new.span_id,
                sampled=new.sampled,
                parent_span_id="",
            )
        else:
            new = new_trace_context(sampled=inbound.sampled)
            ctx = TraceContext(
                trace_id=inbound.trace_id,
                span_id=new.span_id,
                sampled=inbound.sampled,
                parent_span_id=inbound.span_id,
            )
        set_trace_context(ctx)

        # Capture the auth context Traefik forward-auth injected (R-100-118).
        # When a downstream component (e.g. C9 → C5) calls another via
        # `make_traced_client`, the outbound httpx hook re-injects these
        # so the next hop's auth guard sees the same identity.
        set_auth_context(
            user_id=_find_header(scope, b"x-user-id") or "",
            user_roles=_find_header(scope, b"x-user-roles") or "",
            tenant_id=_find_header(scope, b"x-tenant-id") or "",
        )

        method = scope.get("method", "")
        # raw_path is bytes; decode lossily — log readability beats correctness.
        raw_path = scope.get("raw_path") or scope.get("path", "")
        if isinstance(raw_path, bytes):
            path = raw_path.decode("utf-8", errors="replace")
        else:
            path = str(raw_path)

        # Status code is captured in the send wrapper since it sits on
        # the http.response.start message.
        status_code_holder = {"status": 0}
        started = time.perf_counter()

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code_holder["status"] = int(message.get("status", 0))
                headers = list(message.get("headers", []))
                headers.append(
                    (b"traceparent", build_traceparent(ctx).encode("ascii"))
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._app(scope, receive, _send)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            # Emit the span_summary at INFO so it survives even when the
            # debug noise is filtered out. Phase-3 aggregator filters by
            # `event=span_summary`.
            _log.info(
                "span_summary",
                extra={
                    "event": "span_summary",
                    "method": method,
                    "path": path,
                    "status_code": status_code_holder["status"],
                    "duration_ms": round(duration_ms, 3),
                    "parent_span_id": ctx.parent_span_id,
                    "sampled": ctx.sampled,
                },
            )

    def _roll_sampled(self) -> bool:
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        return random.random() < self._sample_rate


def _find_header(scope: Scope, name: bytes) -> str | None:
    name_lower = name.lower()
    for key, value in scope.get("headers", []):
        if key.lower() == name_lower:
            try:
                return str(value.decode("ascii"))
            except UnicodeDecodeError:
                return None
    return None
