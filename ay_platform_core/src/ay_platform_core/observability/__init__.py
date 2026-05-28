# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/__init__.py
# Description: Production-tier observability helpers shared by every
#              backbone component. Provides:
#                - structured-JSON logging (R-100-104)
#                - W3C Trace Context propagation (R-100-105)
#                - a httpx client factory that automatically injects the
#                  current `traceparent` header on every outgoing request.
#
#              NOT to be confused with `_observability/` which is the
#              test-tier log AGGREGATOR (consumes Docker streams). This
#              module is for log EMISSION + trace propagation, runs in
#              production, and contains no Docker socket access.
#
# @relation implements:R-100-104
# @relation implements:R-100-105
# =============================================================================

from ay_platform_core.observability.context import (
    TraceContext,
    build_traceparent,
    current_parent_span_id,
    current_span_id,
    current_tenant_id,
    current_trace_id,
    current_traceparent,
    current_user_id,
    current_user_roles,
    new_trace_context,
    parse_traceparent,
    set_auth_context,
    set_tenant_id,
    set_trace_context,
)
from ay_platform_core.observability.http_client import make_traced_client
from ay_platform_core.observability.middleware import TraceContextMiddleware
from ay_platform_core.observability.setup import configure_logging

__all__ = [
    "TraceContext",
    "TraceContextMiddleware",
    "build_traceparent",
    "configure_logging",
    "current_parent_span_id",
    "current_span_id",
    "current_tenant_id",
    "current_trace_id",
    "current_traceparent",
    "current_user_id",
    "current_user_roles",
    "make_traced_client",
    "new_trace_context",
    "parse_traceparent",
    "set_auth_context",
    "set_tenant_id",
    "set_trace_context",
]
