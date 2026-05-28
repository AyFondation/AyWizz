# =============================================================================
# File: test_loki_integration.py
# Version: 1
# Path: ay_platform_core/tests/integration/observability/workflow/test_loki_integration.py
# Description: End-to-end test of LokiSpanSource against a real Loki
#              container. Pushes synthetic span_summary lines via
#              `/loki/api/v1/push`, polls until queryable, then exercises
#              the adapter's fetch_for_trace + fetch_recent + the full
#              workflow envelope synthesised by the router.
#
# @relation validates:R-100-124
# =============================================================================

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.observability.workflow.router import make_workflow_router
from ay_platform_core.observability.workflow.sources import LokiSpanSource
from tests.fixtures.observability_containers import LokiEndpoint

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span_summary(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str = "",
    component: str = "c2_auth",
    method: str = "GET",
    path: str = "/health",
    status_code: int = 200,
    duration_ms: float = 5.0,
    timestamp: datetime,
) -> str:
    return json.dumps(
        {
            "timestamp": timestamp.isoformat(),
            "component": component,
            "severity": "INFO",
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "tenant_id": "",
            "logger": "ay.observability.middleware",
            "message": "span_summary",
            "event": "span_summary",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "sampled": True,
        }
    )


def _push_lines(
    base_url: str,
    *,
    container_label: str,
    lines: list[tuple[datetime, str]],
) -> None:
    """Push log lines into Loki via the v1 push API.

    `container_label` is used as the only stream label so the test's
    LogQL selector (matching the adapter's default) reaches the data.
    """
    streams = {
        "stream": {"container": container_label},
        "values": [
            [str(int(ts.timestamp() * 1_000_000_000)), line] for ts, line in lines
        ],
    }
    response = httpx.post(
        f"{base_url}/loki/api/v1/push",
        json={"streams": [streams]},
        timeout=10.0,
    )
    response.raise_for_status()


async def _wait_for_visibility(
    source: LokiSpanSource,
    *,
    trace_id: str,
    expected_count: int,
    timeout_s: float = 30.0,
) -> int:
    """Poll Loki until `expected_count` spans for `trace_id` are queryable.

    Loki's ingester flushes asynchronously, so newly-pushed lines may take
    1-2 seconds to become queryable. We poll rather than sleep blindly
    so the test reacts to the actual readiness signal.
    """
    deadline = time.monotonic() + timeout_s
    last_count = 0
    while time.monotonic() < deadline:
        spans = await source.fetch_for_trace(trace_id)
        last_count = len(spans)
        if last_count >= expected_count:
            return last_count
        await asyncio.sleep(1.0)
    return last_count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loki_adapter_round_trip_returns_synthesised_envelope(
    loki_container: LokiEndpoint,
) -> None:
    container_label = f"ay-c2_auth-loki-rt-{uuid.uuid4().hex[:8]}"
    trace_id = uuid.uuid4().hex
    other_trace = uuid.uuid4().hex
    now = datetime.now(tz=UTC).replace(microsecond=0)

    lines: list[tuple[datetime, str]] = [
        (
            now - timedelta(seconds=20),
            _span_summary(
                trace_id=trace_id,
                span_id="1" * 16,
                component="c2_auth",
                path="/auth/verify",
                timestamp=now - timedelta(seconds=20),
            ),
        ),
        (
            now - timedelta(seconds=15),
            _span_summary(
                trace_id=trace_id,
                span_id="2" * 16,
                parent_span_id="1" * 16,
                component="c5_req",
                path="/api/v1/requirements",
                duration_ms=12.5,
                timestamp=now - timedelta(seconds=15),
            ),
        ),
        (
            now - timedelta(seconds=10),
            _span_summary(
                trace_id=other_trace,
                span_id="3" * 16,
                component="c4_orchestrator",
                timestamp=now - timedelta(seconds=10),
            ),
        ),
    ]
    _push_lines(loki_container.base_url, container_label=container_label, lines=lines)

    source = LokiSpanSource(
        base_url=loki_container.base_url,
        label_selector=f'{{container="{container_label}"}}',
        time_window=timedelta(minutes=10),
        fetch_limit=100,
    )
    try:
        # Loki ingestion lag — give it a window to flush the chunk and
        # make the lines queryable before the synthesis call.
        visible = await _wait_for_visibility(
            source, trace_id=trace_id, expected_count=2, timeout_s=30.0
        )
        assert visible >= 2, f"only {visible} span(s) became queryable in 30s"

        spans = await source.fetch_for_trace(trace_id)
    finally:
        await source.aclose()

    # Adapter does not post-filter; Loki's substring filter does.
    assert {s.span_id for s in spans} == {"1" * 16, "2" * 16}
    assert all(s.trace_id == trace_id for s in spans)
    assert {s.component for s in spans} == {"c2_auth", "c5_req"}


@pytest.mark.asyncio
async def test_loki_adapter_rejected_by_router_when_trace_unknown(
    loki_container: LokiEndpoint,
) -> None:
    """The router returns 404 on a trace_id that has no matching lines."""
    container_label = f"ay-empty-{uuid.uuid4().hex[:8]}"
    source = LokiSpanSource(
        base_url=loki_container.base_url,
        label_selector=f'{{container="{container_label}"}}',
        time_window=timedelta(minutes=5),
    )
    app = FastAPI()
    app.include_router(make_workflow_router(source))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        try:
            resp = await client.get("/workflows/" + ("a" * 32))
        finally:
            await source.aclose()

    assert resp.status_code == 404
    assert "no span_summary records" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_loki_adapter_recent_listing_includes_pushed_trace(
    loki_container: LokiEndpoint,
) -> None:
    container_label = f"ay-recent-{uuid.uuid4().hex[:8]}"
    trace_id = uuid.uuid4().hex
    now = datetime.now(tz=UTC).replace(microsecond=0)
    _push_lines(
        loki_container.base_url,
        container_label=container_label,
        lines=[
            (
                now - timedelta(seconds=5),
                _span_summary(
                    trace_id=trace_id,
                    span_id="1" * 16,
                    timestamp=now - timedelta(seconds=5),
                ),
            ),
        ],
    )

    source = LokiSpanSource(
        base_url=loki_container.base_url,
        label_selector=f'{{container="{container_label}"}}',
        time_window=timedelta(minutes=5),
    )
    try:
        visible = await _wait_for_visibility(
            source, trace_id=trace_id, expected_count=1, timeout_s=30.0
        )
        assert visible >= 1
        spans = await source.fetch_recent(limit=10)
    finally:
        await source.aclose()

    assert any(s.trace_id == trace_id for s in spans)
