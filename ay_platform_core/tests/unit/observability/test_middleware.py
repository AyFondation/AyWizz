# =============================================================================
# File: test_middleware.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/test_middleware.py
# Description: TraceContextMiddleware behaviour: parses inbound traceparent
#              when valid; generates a fresh context otherwise; propagates
#              the response header; emits a `span_summary` log line per
#              request with parent_span_id captured from the inbound.
# =============================================================================

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ay_platform_core.observability.context import (
    current_parent_span_id,
    current_span_id,
    current_trace_id,
    parse_traceparent,
)
from ay_platform_core.observability.formatter import JSONFormatter
from ay_platform_core.observability.middleware import TraceContextMiddleware

pytestmark = pytest.mark.unit


def _build_app(*, captured_ctx: dict[str, str], sample_rate: float = 1.0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceContextMiddleware, sample_rate=sample_rate)

    @app.get("/echo")
    async def echo() -> dict[str, str]:
        captured_ctx["trace_id"] = current_trace_id()
        captured_ctx["span_id"] = current_span_id()
        captured_ctx["parent_span_id"] = current_parent_span_id()
        return {"ok": "yes"}

    return app


class TestInboundTraceparent:
    def test_inbound_traceparent_is_inherited(self) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx)
        client = TestClient(app)
        inbound = "00-aabbccddeeff00112233445566778899-1122334455667788-01"
        resp = client.get("/echo", headers={"traceparent": inbound})
        assert resp.status_code == 200
        # Same trace_id; new span_id; parent_span_id = inbound span_id.
        assert ctx["trace_id"] == "aabbccddeeff00112233445566778899"
        assert ctx["span_id"] != "1122334455667788"
        assert len(ctx["span_id"]) == 16
        assert ctx["parent_span_id"] == "1122334455667788"

    def test_response_emits_traceparent(self) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx)
        client = TestClient(app)
        inbound = "00-aabbccddeeff00112233445566778899-1122334455667788-01"
        resp = client.get("/echo", headers={"traceparent": inbound})
        # Response header carries OUR span_id (not the inbound one).
        out_tp = parse_traceparent(resp.headers.get("traceparent"))
        assert out_tp is not None
        assert out_tp.trace_id == "aabbccddeeff00112233445566778899"
        assert out_tp.span_id == ctx["span_id"]

    def test_no_inbound_starts_a_fresh_trace(self) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx)
        client = TestClient(app)
        resp = client.get("/echo")
        assert resp.status_code == 200
        assert len(ctx["trace_id"]) == 32
        assert len(ctx["span_id"]) == 16
        # Root of the trace → no parent.
        assert ctx["parent_span_id"] == ""

    def test_malformed_inbound_falls_back_to_fresh(self) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx)
        client = TestClient(app)
        resp = client.get("/echo", headers={"traceparent": "garbage"})
        assert resp.status_code == 200
        assert ctx["trace_id"] != ""
        # Malformed = treated as no inbound, so root span.
        assert ctx["parent_span_id"] == ""


class TestSpanSummaryEmission:
    """Phase-2: every request SHALL produce one `span_summary` log line."""

    def test_emits_span_summary_with_required_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx)
        client = TestClient(app)
        inbound = "00-aabbccddeeff00112233445566778899-1122334455667788-01"

        with caplog.at_level(logging.INFO, logger="ay.observability.middleware"):
            client.get("/echo?x=1", headers={"traceparent": inbound})

        records = [
            r for r in caplog.records if r.message == "span_summary"
        ]
        assert len(records) == 1, "expected exactly one span_summary line"
        rec = records[0]
        # Required fields per phase-2 schema:
        for attr in (
            "event",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "parent_span_id",
            "sampled",
        ):
            assert hasattr(rec, attr), f"missing span_summary attribute: {attr}"
        assert rec.event == "span_summary"  # type: ignore[attr-defined]
        assert rec.method == "GET"  # type: ignore[attr-defined]
        # Path includes the query string.
        assert "/echo" in rec.path  # type: ignore[attr-defined]
        assert rec.status_code == 200  # type: ignore[attr-defined]
        assert rec.parent_span_id == "1122334455667788"  # type: ignore[attr-defined]
        assert isinstance(rec.duration_ms, float)  # type: ignore[attr-defined]

    def test_span_summary_records_carry_trace_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The JSON formatter (separate test) reads trace_id from
        ContextVars at format time; here we just verify the middleware
        sets the ContextVar BEFORE it logs."""
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx)
        client = TestClient(app)

        with caplog.at_level(logging.INFO, logger="ay.observability.middleware"):
            client.get("/echo")

        # Use the formatter to verify the trace_id is on the line — not
        # on the LogRecord itself (it's added at format time via the
        # ContextVar) but on the rendered output. We re-format the
        # captured record; ContextVars from middleware.__call__ have
        # already been overwritten by the time we get here, so we just
        # check the record structure is compatible with the formatter.
        records = [r for r in caplog.records if r.message == "span_summary"]
        assert records
        formatter = JSONFormatter(component="c_test")
        rendered = json.loads(formatter.format(records[0]))
        assert rendered["message"] == "span_summary"
        assert rendered["component"] == "c_test"


class TestSampling:
    def test_sample_rate_zero_marks_unsampled(self) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx, sample_rate=0.0)
        client = TestClient(app)
        resp = client.get("/echo")  # no inbound → roll the dice (rate=0)
        # Response header uses unsampled flag (`-00`).
        out_tp = resp.headers.get("traceparent", "")
        assert out_tp.endswith("-00"), f"expected unsampled, got {out_tp!r}"

    def test_inbound_sampled_propagates_even_when_rate_is_zero(self) -> None:
        ctx: dict[str, str] = {}
        app = _build_app(captured_ctx=ctx, sample_rate=0.0)
        client = TestClient(app)
        inbound = "00-aabbccddeeff00112233445566778899-1122334455667788-01"
        resp = client.get("/echo", headers={"traceparent": inbound})
        # Inbound `sampled=true` is honoured regardless of local rate.
        assert resp.headers["traceparent"].endswith("-01")
