# =============================================================================
# File: test_router.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/workflow/test_router.py
# Description: Unit tests for `make_workflow_router`. Mounts the router on
#              a stub `SpanSource` and exercises the HTTP surface
#              (validation, 404 on empty, envelope shape, recent listing).
#
# @relation validates:R-100-124
# =============================================================================

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core._observability.synthesis import Span
from ay_platform_core.observability.workflow.router import make_workflow_router

pytestmark = pytest.mark.unit


class _StubSource:
    """In-memory SpanSource for router tests. No HTTP, no buffer."""

    def __init__(self, spans: Sequence[Span] = ()) -> None:
        self._spans = list(spans)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_for_trace(self, trace_id: str) -> list[Span]:
        self.calls.append(("fetch_for_trace", {"trace_id": trace_id}))
        return [s for s in self._spans if s.trace_id == trace_id]

    async def fetch_recent(
        self,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Span]:
        self.calls.append(
            ("fetch_recent", {"since": since, "limit": limit}),
        )
        return list(self._spans)[:limit]

    async def aclose(self) -> None:
        return None


def _make_span(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str = "",
    component: str = "c2_auth",
    status_code: int = 200,
    started_offset_ms: float = 0.0,
) -> Span:
    base = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        component=component,
        method="GET",
        path="/x",
        status_code=status_code,
        duration_ms=5.0,
        sampled=True,
        started_at=base + timedelta(milliseconds=started_offset_ms),
    )


def _client(source: _StubSource) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(make_workflow_router(source))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


# ---------------------------------------------------------------------------
# /workflows/{trace_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_envelope_returned_for_known_trace() -> None:
    trace = "a" * 32
    source = _StubSource(
        spans=[
            _make_span(trace_id=trace, span_id="1" * 16),
            _make_span(
                trace_id=trace,
                span_id="2" * 16,
                parent_span_id="1" * 16,
                component="c5_req",
                started_offset_ms=10,
            ),
        ],
    )
    async with _client(source) as client:
        resp = await client.get(f"/workflows/{trace}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["trace_id"] == trace
    assert len(body["spans"]) == 2
    assert sorted(body["summary"]["components_touched"]) == ["c2_auth", "c5_req"]
    assert body["summary"]["verdict"] == "ok"


@pytest.mark.asyncio
async def test_workflow_returns_404_when_no_spans_for_trace() -> None:
    source = _StubSource(spans=[])
    async with _client(source) as client:
        resp = await client.get("/workflows/" + ("d" * 32))

    assert resp.status_code == 404
    assert "no span_summary records" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trace_id",
    [
        "x" * 32,  # non-hex
        "a" * 31,  # too short
        "a" * 33,  # too long
        "A" * 32,  # uppercase rejected (W3C trace_id is lowercase hex)
    ],
)
async def test_workflow_rejects_malformed_trace_id(trace_id: str) -> None:
    source = _StubSource()
    async with _client(source) as client:
        resp = await client.get(f"/workflows/{trace_id}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /workflows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflows_list_returns_recent_summaries() -> None:
    trace_a = "a" * 32
    trace_b = "b" * 32
    source = _StubSource(
        spans=[
            _make_span(trace_id=trace_a, span_id="1" * 16, started_offset_ms=0),
            _make_span(trace_id=trace_a, span_id="2" * 16, started_offset_ms=5),
            _make_span(trace_id=trace_b, span_id="3" * 16, started_offset_ms=20),
        ],
    )
    async with _client(source) as client:
        resp = await client.get("/workflows", params={"recent": 5})

    assert resp.status_code == 200
    summaries = resp.json()
    assert isinstance(summaries, list)
    assert {s["trace_id"] for s in summaries} == {trace_a, trace_b}
    # Sorted by ended_at descending: trace_b ended later (offset 20 + 5ms).
    assert summaries[0]["trace_id"] == trace_b


@pytest.mark.asyncio
async def test_workflows_list_propagates_fetch_limit() -> None:
    source = _StubSource()
    async with _client(source) as client:
        resp = await client.get("/workflows", params={"recent": 3, "fetch_limit": 50})

    assert resp.status_code == 200
    assert source.calls[-1] == ("fetch_recent", {"since": None, "limit": 50})


@pytest.mark.asyncio
async def test_workflows_list_validates_query_bounds() -> None:
    source = _StubSource()
    async with _client(source) as client:
        # recent must be 1..200
        bad = await client.get("/workflows", params={"recent": 0})
        assert bad.status_code == 422
        too_big = await client.get("/workflows", params={"recent": 201})
        assert too_big.status_code == 422
