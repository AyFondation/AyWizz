# =============================================================================
# File: test_synthesis.py
# Version: 1
# Path: ay_platform_core/tests/unit/_observability/test_synthesis.py
# Description: Workflow envelope synthesiser (Q-100-014). Tests are pure-
#              function: no FastAPI, no buffer, no Docker — just
#              `parse_span_summary`, `group_by_trace`, `synthesise_workflow`,
#              `list_recent_traces`.
# =============================================================================

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from ay_platform_core._observability.synthesis import (
    Span,
    group_by_trace,
    list_recent_traces,
    parse_lines,
    parse_span_summary,
    synthesise_workflow,
)

pytestmark = pytest.mark.unit


def _summary_line(
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
) -> str:
    """Build a span_summary log line as the JSONFormatter would emit."""
    return json.dumps(
        {
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
    )


def _make_span(
    *,
    trace_id: str = "a" * 32,
    span_id: str = "1" * 16,
    parent_span_id: str = "",
    component: str = "c2_auth",
    method: str = "GET",
    path: str = "/x",
    status_code: int = 200,
    duration_ms: float = 5.0,
    started_offset_ms: float = 0,
) -> Span:
    base = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        component=component,
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=duration_ms,
        sampled=True,
        started_at=base + timedelta(milliseconds=started_offset_ms),
    )


class TestParseSpanSummary:
    def test_parses_valid_line(self) -> None:
        span = parse_span_summary(_summary_line(span_id="b" * 16))
        assert span is not None
        assert span.trace_id == "a" * 32
        assert span.span_id == "b" * 16
        assert span.method == "GET"
        assert span.path == "/health"
        assert span.status_code == 200

    def test_started_at_is_timestamp_minus_duration(self) -> None:
        span = parse_span_summary(
            _summary_line(
                timestamp="2026-04-25T12:00:00.500+00:00", duration_ms=500.0
            )
        )
        assert span is not None
        assert span.started_at == datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)

    def test_non_span_summary_event_returns_none(self) -> None:
        line = json.dumps(
            {
                "event": "something_else",
                "timestamp": "2026-04-25T12:00:00+00:00",
                "trace_id": "x" * 32,
            }
        )
        assert parse_span_summary(line) is None

    def test_no_event_field_returns_none(self) -> None:
        line = json.dumps({"timestamp": "2026-04-25T12:00:00+00:00", "msg": "hi"})
        assert parse_span_summary(line) is None

    def test_malformed_json_returns_none(self) -> None:
        assert parse_span_summary("{not really json}") is None
        assert parse_span_summary("") is None

    def test_non_object_json_returns_none(self) -> None:
        assert parse_span_summary("[1,2,3]") is None

    def test_invalid_timestamp_returns_none(self) -> None:
        line = json.dumps(
            {
                "event": "span_summary",
                "timestamp": "not-a-date",
                "trace_id": "x" * 32,
                "span_id": "y" * 16,
                "duration_ms": 1.0,
            }
        )
        assert parse_span_summary(line) is None

    def test_parse_lines_filters_silently(self) -> None:
        spans = parse_lines(
            [
                _summary_line(span_id="a" * 16),
                "garbage",
                json.dumps({"event": "other"}),
                _summary_line(span_id="b" * 16),
            ]
        )
        assert [s.span_id for s in spans] == ["a" * 16, "b" * 16]


class TestGroupByTrace:
    def test_groups_by_trace_id(self) -> None:
        spans = [
            _make_span(trace_id="t1", span_id="a" * 16),
            _make_span(trace_id="t2", span_id="b" * 16),
            _make_span(trace_id="t1", span_id="c" * 16),
        ]
        out = group_by_trace(spans)
        assert sorted(out.keys()) == ["t1", "t2"]
        assert len(out["t1"]) == 2
        assert len(out["t2"]) == 1

    def test_empty_trace_id_skipped(self) -> None:
        spans = [
            _make_span(trace_id="", span_id="a" * 16),
            _make_span(trace_id="t1", span_id="b" * 16),
        ]
        out = group_by_trace(spans)
        assert list(out.keys()) == ["t1"]


class TestSynthesise:
    def test_two_span_chain_renders_envelope(self) -> None:
        # Front span (root)
        front = _make_span(
            span_id="aaaaaaaaaaaaaaaa",
            parent_span_id="",
            component="c9_mcp",
            method="POST",
            path="/api/v1/mcp/initialize",
            duration_ms=80.0,
            started_offset_ms=0,
        )
        # Back span (child)
        back = _make_span(
            span_id="bbbbbbbbbbbbbbbb",
            parent_span_id=front.span_id,
            component="c5_requirements",
            method="GET",
            path="/api/v1/requirements/demo",
            duration_ms=20.0,
            started_offset_ms=10,
        )
        env = synthesise_workflow([back, front])  # unsorted on purpose

        assert env["trace_id"] == front.trace_id
        # Sorted chronologically: front first, back second.
        assert [s["span_id"] for s in env["spans"]] == [
            front.span_id,
            back.span_id,
        ]
        assert env["root_span_id"] == front.span_id
        assert env["summary"]["components_touched"] == [
            "c5_requirements",
            "c9_mcp",
        ]
        assert env["summary"]["total_spans"] == 2
        assert env["summary"]["errors"] == 0
        assert env["summary"]["verdict"] == "ok"
        # duration is wall-clock from earliest start to latest end.
        # back ends at 10+20=30ms; front ends at 0+80=80ms; total = 80ms.
        assert env["duration_ms"] == 80.0

    def test_verdict_error_when_5xx(self) -> None:
        spans = [
            _make_span(span_id="a" * 16, status_code=200),
            _make_span(span_id="b" * 16, status_code=503, parent_span_id="a" * 16),
        ]
        env = synthesise_workflow(spans)
        assert env["summary"]["errors"] == 1
        assert env["summary"]["verdict"] == "error"

    def test_verdict_warn_when_only_4xx(self) -> None:
        spans = [
            _make_span(span_id="a" * 16, status_code=200),
            _make_span(span_id="b" * 16, status_code=401, parent_span_id="a" * 16),
        ]
        env = synthesise_workflow(spans)
        assert env["summary"]["errors"] == 0
        assert env["summary"]["warnings"] == 1
        assert env["summary"]["verdict"] == "warn"

    def test_root_falls_back_to_earliest_when_no_parentless(self) -> None:
        # Edge case: someone synthesises mid-trace records (no inbound
        # span captured). Root should be the chronologically earliest.
        spans = [
            _make_span(span_id="a" * 16, parent_span_id="x" * 16, started_offset_ms=10),
            _make_span(span_id="b" * 16, parent_span_id="a" * 16, started_offset_ms=20),
        ]
        env = synthesise_workflow(spans)
        assert env["root_span_id"] == "a" * 16

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError):
            synthesise_workflow([])

    def test_mixed_traces_raises(self) -> None:
        spans = [
            _make_span(trace_id="t1", span_id="a" * 16),
            _make_span(trace_id="t2", span_id="b" * 16),
        ]
        with pytest.raises(ValueError):
            synthesise_workflow(spans)


class TestListRecentTraces:
    def test_sorted_by_ended_at_desc(self) -> None:
        # Three traces, end times 100ms / 200ms / 50ms after the base.
        spans = [
            _make_span(trace_id="t1", span_id="1" * 16, started_offset_ms=0, duration_ms=100),
            _make_span(trace_id="t2", span_id="2" * 16, started_offset_ms=0, duration_ms=200),
            _make_span(trace_id="t3", span_id="3" * 16, started_offset_ms=0, duration_ms=50),
        ]
        out = list_recent_traces(spans, limit=10)
        assert [t["trace_id"] for t in out] == ["t2", "t1", "t3"]

    def test_respects_limit(self) -> None:
        spans = [
            _make_span(trace_id=f"t{i}", span_id=f"{i:016d}", duration_ms=i)
            for i in range(1, 6)
        ]
        out = list_recent_traces(spans, limit=2)
        assert len(out) == 2

    def test_empty_input_returns_empty(self) -> None:
        assert list_recent_traces([], limit=10) == []
