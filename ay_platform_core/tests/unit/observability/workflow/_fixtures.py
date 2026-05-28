# =============================================================================
# File: _fixtures.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/workflow/_fixtures.py
# Description: Shared helpers for workflow source tests. Builds
#              span_summary records in the JSON shape emitted by
#              `observability.formatter.JSONFormatter` (R-100-104).
# =============================================================================

from __future__ import annotations

import json
from typing import Any


def make_span_summary(
    *,
    trace_id: str = "a" * 32,
    span_id: str = "1" * 16,
    parent_span_id: str = "",
    component: str = "c2_auth",
    method: str = "GET",
    path: str = "/health",
    status_code: int = 200,
    duration_ms: float = 5.0,
    timestamp: str = "2026-04-25T12:00:00.000+00:00",
    sampled: bool = True,
) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "component": component,
        "severity": "INFO",
        "trace_id": trace_id,
        "span_id": span_id,
        "tenant_id": "",
        "logger": "ay.observability.middleware",
        "message": "span_summary",
        "event": "span_summary",
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "parent_span_id": parent_span_id,
        "sampled": sampled,
    }


def make_span_summary_line(**kwargs: Any) -> str:
    return json.dumps(make_span_summary(**kwargs))
