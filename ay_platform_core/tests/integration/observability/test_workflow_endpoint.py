# =============================================================================
# File: test_workflow_endpoint.py
# Version: 1
# Path: ay_platform_core/tests/integration/observability/test_workflow_endpoint.py
# Description: Phase-3 endpoint tests (Q-100-014). Spins the test-tier
#              `_observability` app in-process, monkey-patches the
#              LogCollector so no Docker socket is touched, pre-seeds
#              the ring buffer with synthetic span_summary lines, and
#              exercises GET /workflows, GET /workflows/<trace_id>,
#              and the malformed/unknown error paths.
# =============================================================================

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ay_platform_core._observability.buffer import LogEntry
from ay_platform_core._observability.main import (
    ObservabilityConfig,
    create_app,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan invokes LogCollector.start/stop; without Docker in
    the test devcontainer-of-devcontainer this would fail. Replace
    both with no-ops — the buffer is what we pre-seed manually."""
    monkeypatch.setattr(
        "ay_platform_core._observability.collector.LogCollector.start",
        lambda self: None,
    )
    monkeypatch.setattr(
        "ay_platform_core._observability.collector.LogCollector.stop",
        lambda self: None,
    )


def _summary_entry(
    *,
    component: str,
    trace_id: str,
    span_id: str,
    parent_span_id: str = "",
    method: str = "GET",
    path: str = "/x",
    status_code: int = 200,
    duration_ms: float = 5.0,
    seconds_offset: float = 0.0,
) -> LogEntry:
    base = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
    timestamp = base + timedelta(seconds=seconds_offset)
    line = json.dumps(
        {
            "timestamp": timestamp.isoformat(),
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
            "sampled": True,
        }
    )
    return LogEntry(
        service=component, timestamp=timestamp, line=line, severity="INFO"
    )


@pytest.fixture(scope="function")
def seeded_app() -> Iterator[tuple[TestClient, str]]:
    """Build the app, pre-seed two traces, return a TestClient + the
    trace_id of the multi-span trace (so tests can target it)."""
    app = create_app(ObservabilityConfig())

    trace_a = "a" * 32
    trace_b = "b" * 32
    buffer = app.state.log_buffer

    # Trace A: c9 (root) → c5 (child)
    buffer.append(
        _summary_entry(
            component="c9_mcp",
            trace_id=trace_a,
            span_id="1" * 16,
            parent_span_id="",
            method="POST",
            path="/api/v1/mcp/initialize",
            status_code=200,
            duration_ms=80.0,
            seconds_offset=0.0,
        )
    )
    buffer.append(
        _summary_entry(
            component="c5_requirements",
            trace_id=trace_a,
            span_id="2" * 16,
            parent_span_id="1" * 16,
            method="GET",
            path="/api/v1/requirements/demo",
            status_code=200,
            duration_ms=20.0,
            seconds_offset=0.01,
        )
    )

    # Trace B: a single span with a 500
    buffer.append(
        _summary_entry(
            component="c2_auth",
            trace_id=trace_b,
            span_id="3" * 16,
            parent_span_id="",
            method="POST",
            path="/auth/login",
            status_code=503,
            duration_ms=15.0,
            seconds_offset=10.0,  # 10 s after trace A
        )
    )

    # The TestClient context manager invokes the lifespan; collector is
    # patched to no-op so no Docker call happens.
    with TestClient(app) as client:
        yield client, trace_a


class TestWorkflowEnvelope:
    def test_returns_envelope_for_known_trace(
        self, seeded_app: tuple[TestClient, str]
    ) -> None:
        client, trace_a = seeded_app
        resp = client.get(f"/workflows/{trace_a}")
        assert resp.status_code == 200
        env = resp.json()

        assert env["trace_id"] == trace_a
        assert env["root_span_id"] == "1" * 16
        # 2 spans, sorted chronologically.
        assert [s["span_id"] for s in env["spans"]] == ["1" * 16, "2" * 16]
        # Both children carry parent_span_id correctly.
        assert env["spans"][0]["parent_span_id"] == ""
        assert env["spans"][1]["parent_span_id"] == "1" * 16
        # Summary block.
        assert env["summary"]["components_touched"] == [
            "c5_requirements",
            "c9_mcp",
        ]
        assert env["summary"]["total_spans"] == 2
        assert env["summary"]["errors"] == 0
        assert env["summary"]["verdict"] == "ok"
        # Operation hint composed by the synthesiser.
        assert env["spans"][0]["operation"] == "POST /api/v1/mcp/initialize"

    def test_unknown_trace_returns_404(
        self, seeded_app: tuple[TestClient, str]
    ) -> None:
        client, _ = seeded_app
        unknown = "f" * 32
        resp = client.get(f"/workflows/{unknown}")
        assert resp.status_code == 404
        assert "no span_summary records" in resp.json()["detail"]

    def test_malformed_trace_id_returns_400(
        self, seeded_app: tuple[TestClient, str]
    ) -> None:
        client, _ = seeded_app
        resp = client.get("/workflows/short")
        assert resp.status_code == 400
        assert "32 hex" in resp.json()["detail"]


class TestWorkflowsList:
    def test_returns_recent_summaries(
        self, seeded_app: tuple[TestClient, str]
    ) -> None:
        client, trace_a = seeded_app
        resp = client.get("/workflows?recent=10")
        assert resp.status_code == 200
        traces = resp.json()
        assert isinstance(traces, list)
        # Two traces seeded; trace B started 10s AFTER trace A so it
        # comes first in DESC order.
        assert len(traces) == 2
        assert traces[0]["verdict"] == "error"  # trace B (503)
        assert traces[1]["trace_id"] == trace_a  # trace A (ok)
        # Each summary is compact: exactly the documented fields.
        sample = traces[0]
        for key in (
            "trace_id",
            "started_at",
            "ended_at",
            "duration_ms",
            "total_spans",
            "components_touched",
            "verdict",
        ):
            assert key in sample, f"missing {key!r} in summary"

    def test_recent_limit_caps_count(
        self, seeded_app: tuple[TestClient, str]
    ) -> None:
        client, _ = seeded_app
        resp = client.get("/workflows?recent=1")
        assert resp.status_code == 200
        traces = resp.json()
        assert len(traces) == 1

    def test_default_limit(self, seeded_app: tuple[TestClient, str]) -> None:
        client, _ = seeded_app
        resp = client.get("/workflows")  # no `recent=` → default 10
        assert resp.status_code == 200
        # Two seeded traces, both fit under 10.
        assert len(resp.json()) == 2
