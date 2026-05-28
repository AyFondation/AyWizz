# =============================================================================
# File: formatter.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/formatter.py
# Description: `logging.Formatter` subclass that emits one JSON object per
#              log record. Every line carries the mandatory fields from
#              R-100-104: timestamp, component, severity, trace_id,
#              span_id, tenant_id, message. Free-form `extra={…}`
#              attributes are merged into the JSON object so call sites
#              can attach structured payloads without bespoke handling.
#
#              Companion `TextFormatter` mirrors the field set in a
#              human-readable form (used in dev when `LOG_FORMAT=text`).
#
# @relation implements:R-100-104
# =============================================================================

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from ay_platform_core.observability.context import (
    current_span_id,
    current_tenant_id,
    current_trace_id,
)

# Keys that `logging.LogRecord` puts on the record by default; we treat
# anything OUTSIDE this set on a record's `__dict__` as caller-provided
# `extra={…}` and merge it into the JSON object.
_LOGRECORD_DEFAULT_KEYS: frozenset[str] = frozenset(
    logging.LogRecord(
        name="x", level=0, pathname="x", lineno=0, msg="", args=None, exc_info=None
    ).__dict__.keys()
) | {"message", "asctime"}


class JSONFormatter(logging.Formatter):
    """Render every record as a single JSON line.

    Mandatory fields (R-100-104):
      timestamp, component, severity, trace_id, span_id, tenant_id, message
    """

    def __init__(self, component: str) -> None:
        super().__init__()
        self._component = component

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "component": self._component,
            "severity": record.levelname,
            "trace_id": current_trace_id(),
            "span_id": current_span_id(),
            "tenant_id": current_tenant_id(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Merge `extra=` attributes provided by the call site.
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_DEFAULT_KEYS or key in payload:
                continue
            payload[key] = _make_json_safe(value)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Single-line text formatter mirroring the JSON field set.

    Used in dev when `LOG_FORMAT=text`. Field order is fixed for grep
    friendliness; missing trace identifiers render as `-`.
    """

    def __init__(self, component: str) -> None:
        super().__init__()
        self._component = component

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        trace = current_trace_id() or "-"
        # Short trace id (first 8 chars) is enough for visual correlation
        # across components in a tail; the full id is in the JSON form.
        trace_short = trace[:8] if trace != "-" else "-"
        span = current_span_id()[:8] if current_span_id() else "-"
        tenant = current_tenant_id() or "-"
        line = (
            f"{ts} {record.levelname:<8} "
            f"{self._component:<18} "
            f"trace={trace_short} span={span} tenant={tenant} "
            f"| {record.getMessage()}"
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _make_json_safe(value: Any) -> Any:
    """Best-effort JSON-safe coercion — falls back to str() so the
    formatter never raises on `extra={…}` payloads with exotic types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _make_json_safe(v) for k, v in value.items()}
    return str(value)
