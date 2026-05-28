# =============================================================================
# File: test_context.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/test_context.py
# Description: ContextVar set/get + W3C Trace Context parsing.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.observability.context import (
    TraceContext,
    build_traceparent,
    current_parent_span_id,
    current_span_id,
    current_trace_id,
    current_traceparent,
    new_trace_context,
    parse_traceparent,
    set_trace_context,
)

pytestmark = pytest.mark.unit


class TestTraceparentParser:
    def test_parses_canonical_value(self) -> None:
        ctx = parse_traceparent("00-0123456789abcdef0123456789abcdef-fedcba9876543210-01")
        assert ctx is not None
        assert ctx.trace_id == "0123456789abcdef0123456789abcdef"
        assert ctx.span_id == "fedcba9876543210"
        assert ctx.sampled is True

    def test_parses_unsampled(self) -> None:
        ctx = parse_traceparent(
            "00-0123456789abcdef0123456789abcdef-fedcba9876543210-00"
        )
        assert ctx is not None
        assert ctx.sampled is False

    def test_uppercase_normalised_to_lowercase(self) -> None:
        ctx = parse_traceparent(
            "00-AABBCCDDEEFF00112233445566778899-AABBCCDDEEFF0011-01"
        )
        assert ctx is not None
        assert ctx.trace_id == "aabbccddeeff00112233445566778899"

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "garbage",
            "00-too-short-01",
            # Wrong version
            "ff-0123456789abcdef0123456789abcdef-fedcba9876543210-01",
            # Bad trace-id length
            "00-12345-fedcba9876543210-01",
            # Non-hex chars
            "00-0123456789abcdef0123456789abcdef-fedcba98765432zz-01",
            # Invalid all-zero trace
            "00-00000000000000000000000000000000-fedcba9876543210-01",
            # Invalid all-zero span
            "00-0123456789abcdef0123456789abcdef-0000000000000000-01",
        ],
        ids=[
            "none",
            "empty",
            "garbage",
            "wrong-shape",
            "wrong-version",
            "short-trace",
            "non-hex",
            "zero-trace",
            "zero-span",
        ],
    )
    def test_invalid_returns_none(self, value: str | None) -> None:
        assert parse_traceparent(value) is None


class TestBuildTraceparent:
    def test_round_trip(self) -> None:
        ctx = TraceContext(
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="fedcba9876543210",
            sampled=True,
        )
        out = build_traceparent(ctx)
        re_parsed = parse_traceparent(out)
        assert re_parsed is not None
        assert re_parsed.trace_id == ctx.trace_id
        assert re_parsed.span_id == ctx.span_id
        assert re_parsed.sampled is True

    def test_unsampled_flag(self) -> None:
        ctx = TraceContext(
            trace_id="11111111111111111111111111111111",
            span_id="2222222222222222",
            sampled=False,
        )
        assert build_traceparent(ctx).endswith("-00")


class TestNewTraceContext:
    def test_generates_random_ids(self) -> None:
        a = new_trace_context()
        b = new_trace_context()
        assert a.trace_id != b.trace_id
        assert a.span_id != b.span_id
        assert len(a.trace_id) == 32
        assert len(a.span_id) == 16
        assert a.sampled is True


class TestContextVars:
    def test_default_empty(self) -> None:
        # Outside any request scope, the accessors return empty strings.
        # Note: contextvars are isolated per Task; running this in pytest's
        # task gets its own copy.
        assert current_trace_id() == ""
        assert current_span_id() == ""
        assert current_parent_span_id() == ""
        assert current_traceparent() == ""

    def test_set_then_read_with_parent(self) -> None:
        ctx = TraceContext(
            trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="bbbbbbbbbbbbbbbb",
            sampled=True,
            parent_span_id="ccccccccccccc000",
        )
        set_trace_context(ctx)
        assert current_trace_id() == ctx.trace_id
        assert current_span_id() == ctx.span_id
        assert current_parent_span_id() == ctx.parent_span_id

    def test_current_traceparent_uses_current_span(self) -> None:
        ctx = TraceContext(
            trace_id="0123456789abcdef0123456789abcdef",
            span_id="fedcba9876543210",
            sampled=True,
        )
        set_trace_context(ctx)
        assert current_traceparent() == build_traceparent(ctx)
