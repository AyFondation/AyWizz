# =============================================================================
# File: context.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/context.py
# Description: ContextVars holding the current request's trace identifiers,
#              plus W3C Trace Context (`traceparent`) parsing and
#              construction. ContextVars are isolated per asyncio Task, so
#              each FastAPI request lives in its own context without any
#              extra plumbing.
#
# Reference: https://www.w3.org/TR/trace-context/
#
# @relation implements:R-100-105
# =============================================================================

from __future__ import annotations

import secrets
from contextvars import ContextVar
from dataclasses import dataclass

# W3C Trace Context: traceparent value is "<version>-<trace-id>-<span-id>-<flags>"
# version: 00 (current spec)
# trace-id: 32 hex chars (16 bytes), MUST NOT be all zeros
# span-id: 16 hex chars (8 bytes), MUST NOT be all zeros
# trace-flags: 2 hex chars; bit 0 = sampled
_TRACEPARENT_VERSION = "00"
_INVALID_TRACE_ID = "0" * 32
_INVALID_SPAN_ID = "0" * 16


# Per-request context. Components SHALL NOT read these directly in
# business logic; they are consumed by the JSON formatter (to enrich
# every log line) and by the httpx client factory (to propagate
# downstream).
_trace_id_var: ContextVar[str] = ContextVar("ay_trace_id", default="")
_span_id_var: ContextVar[str] = ContextVar("ay_span_id", default="")
_parent_span_id_var: ContextVar[str] = ContextVar("ay_parent_span_id", default="")
_tenant_id_var: ContextVar[str] = ContextVar("ay_tenant_id", default="")
# Auth context (R-100-118) propagated by Traefik forward-auth into the
# inbound request as X-User-Id / X-User-Roles. Components that fan out
# to other components MUST forward these so the downstream's auth
# guard sees the same identity (otherwise the downstream returns 401
# "X-User-Id header missing").
_user_id_var: ContextVar[str] = ContextVar("ay_user_id", default="")
_user_roles_var: ContextVar[str] = ContextVar("ay_user_roles", default="")


@dataclass(frozen=True)
class TraceContext:
    """Immutable bundle captured at the start of a request.

    `parent_span_id` is the inbound span — empty string when this
    request is the root of the trace. The phase-2 workflow synthesiser
    uses it to reconstruct the parent/child tree across components.
    """

    trace_id: str
    span_id: str
    sampled: bool
    parent_span_id: str = ""


# ---------------------------------------------------------------------------
# Generation + parsing
# ---------------------------------------------------------------------------


def _new_hex(length_bytes: int) -> str:
    """Cryptographically random hex string of `length_bytes` bytes."""
    return secrets.token_hex(length_bytes)


def new_trace_context(*, sampled: bool = True) -> TraceContext:
    """Generate a fresh `(trace_id, span_id, sampled)` triple."""
    return TraceContext(
        trace_id=_new_hex(16),
        span_id=_new_hex(8),
        sampled=sampled,
    )


def parse_traceparent(value: str | None) -> TraceContext | None:  # noqa: PLR0911 — lenient parser: each malformed-input shape returns None separately for clarity
    """Parse a W3C `traceparent` header value.

    Returns ``None`` for any malformed input (the caller SHOULD then
    generate a fresh context). Lenient about case (W3C mandates lower-
    case hex but some senders deviate).
    """
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, span_id, flags = parts
    if version != _TRACEPARENT_VERSION:
        return None
    trace_id = trace_id.lower()
    span_id = span_id.lower()
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    if not all(c in "0123456789abcdef" for c in trace_id):
        return None
    if not all(c in "0123456789abcdef" for c in span_id):
        return None
    if trace_id == _INVALID_TRACE_ID or span_id == _INVALID_SPAN_ID:
        return None
    if len(flags) != 2 or not all(c in "0123456789abcdef" for c in flags.lower()):
        return None
    sampled = bool(int(flags, 16) & 0x01)
    return TraceContext(trace_id=trace_id, span_id=span_id, sampled=sampled)


def build_traceparent(ctx: TraceContext) -> str:
    """Construct a `traceparent` header value from a context."""
    flags = "01" if ctx.sampled else "00"
    return f"{_TRACEPARENT_VERSION}-{ctx.trace_id}-{ctx.span_id}-{flags}"


# ---------------------------------------------------------------------------
# Context accessors
# ---------------------------------------------------------------------------


def set_trace_context(ctx: TraceContext) -> None:
    """Install a TraceContext for the current request scope.

    Called by the FastAPI middleware exactly once per inbound request.
    Subsequent reads via `current_trace_id()` / `current_span_id()` /
    `current_parent_span_id()` return the values until the request ends.
    """
    _trace_id_var.set(ctx.trace_id)
    _span_id_var.set(ctx.span_id)
    _parent_span_id_var.set(ctx.parent_span_id)


def set_tenant_id(tenant_id: str) -> None:
    """Set the tenant id for the current scope (typically from JWT)."""
    _tenant_id_var.set(tenant_id)


def set_auth_context(*, user_id: str, user_roles: str, tenant_id: str) -> None:
    """Set the auth context bundle (called by `TraceContextMiddleware` once
    per inbound request after parsing X-User-Id / X-User-Roles /
    X-Tenant-Id forwarded by Traefik forward-auth)."""
    _user_id_var.set(user_id)
    _user_roles_var.set(user_roles)
    _tenant_id_var.set(tenant_id)


def current_trace_id() -> str:
    """Empty string when no request scope is active."""
    return _trace_id_var.get()


def current_span_id() -> str:
    return _span_id_var.get()


def current_parent_span_id() -> str:
    """Inbound span when this request is a child; empty for trace roots."""
    return _parent_span_id_var.get()


def current_tenant_id() -> str:
    return _tenant_id_var.get()


def current_user_id() -> str:
    return _user_id_var.get()


def current_user_roles() -> str:
    """Comma-separated list of role names (matches the X-User-Roles header)."""
    return _user_roles_var.get()


def current_traceparent() -> str:
    """Build a `traceparent` from the current ContextVars, or empty string.

    Used by the httpx client factory to inject the header on every
    outgoing request so downstream components join the same trace.
    """
    trace_id = current_trace_id()
    span_id = current_span_id()
    if not trace_id or not span_id:
        return ""
    return build_traceparent(
        TraceContext(trace_id=trace_id, span_id=span_id, sampled=True)
    )
