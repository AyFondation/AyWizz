# =============================================================================
# File: test_formatter.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/test_formatter.py
# Description: JSONFormatter + TextFormatter behaviour.
# =============================================================================

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import pytest

from ay_platform_core.observability.context import (
    TraceContext,
    set_trace_context,
)
from ay_platform_core.observability.formatter import JSONFormatter, TextFormatter

pytestmark = pytest.mark.unit


def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    extra: dict[str, Any] | None = None,
    # exc_info matches what `sys.exc_info()` returns: a 3-tuple of
    # (type, value, tb) or all-None. Typed `Any` to dodge the noisy
    # union; the formatter accepts whatever logging.LogRecord accepts.
    exc_info: Any = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="ay.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=exc_info,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


class TestJSONFormatter:
    def test_mandatory_fields_present(self) -> None:
        ctx = TraceContext(
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="fedcba9876543210",
            sampled=True,
        )
        set_trace_context(ctx)
        formatter = JSONFormatter(component="c2_auth")
        out = json.loads(formatter.format(_make_record("login ok")))
        # R-100-104 mandatory fields:
        for key in (
            "timestamp",
            "component",
            "severity",
            "trace_id",
            "span_id",
            "tenant_id",
            "message",
        ):
            assert key in out, f"missing required field: {key}"
        assert out["component"] == "c2_auth"
        assert out["severity"] == "INFO"
        assert out["trace_id"] == ctx.trace_id
        assert out["span_id"] == ctx.span_id
        assert out["message"] == "login ok"

    def test_extra_fields_merged(self) -> None:
        formatter = JSONFormatter(component="c2_auth")
        out = json.loads(
            formatter.format(
                _make_record(
                    "issued JWT",
                    extra={"user_id": "u-1", "jti": "j-1", "ttl": 3600},
                )
            )
        )
        assert out["user_id"] == "u-1"
        assert out["jti"] == "j-1"
        assert out["ttl"] == 3600

    def test_exotic_extra_value_falls_back_to_str(self) -> None:
        class _Weird:
            def __repr__(self) -> str:
                return "<weird>"

        formatter = JSONFormatter(component="c2_auth")
        out = json.loads(
            formatter.format(_make_record(extra={"weird": _Weird()}))
        )
        assert out["weird"] == "<weird>"

    def test_severity_levels(self) -> None:
        formatter = JSONFormatter(component="c4_orchestrator")
        for level, name in (
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ):
            out = json.loads(formatter.format(_make_record(level=level)))
            assert out["severity"] == name

    def test_exception_renders_in_exc_info(self) -> None:
        formatter = JSONFormatter(component="c2_auth")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            exc_info = sys.exc_info()
        out = json.loads(formatter.format(_make_record(exc_info=exc_info)))
        assert "exc_info" in out
        assert "RuntimeError" in out["exc_info"]
        assert "boom" in out["exc_info"]


class TestTextFormatter:
    def test_one_line_format(self) -> None:
        formatter = TextFormatter(component="c2_auth")
        line = formatter.format(_make_record("login ok"))
        assert "\n" not in line
        assert "INFO" in line
        assert "c2_auth" in line
        assert "login ok" in line

    def test_short_trace_id(self) -> None:
        ctx = TraceContext(
            trace_id="abcdef0123456789abcdef0123456789",
            span_id="1111222233334444",
            sampled=True,
        )
        set_trace_context(ctx)
        formatter = TextFormatter(component="c2_auth")
        line = formatter.format(_make_record("hi"))
        assert "trace=abcdef01" in line  # first 8 chars
        assert "span=11112222" in line
