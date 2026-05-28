# =============================================================================
# File: test_elasticsearch_integration.py
# Version: 1
# Path: ay_platform_core/tests/integration/observability/workflow/test_elasticsearch_integration.py
# Description: End-to-end test of ElasticsearchSpanSource against a real
#              ES container. Bulk-indexes synthetic span_summary
#              documents with `?refresh=wait_for` so they are
#              immediately searchable, then exercises the adapter.
#
# @relation validates:R-100-124
# =============================================================================

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.observability.workflow.router import make_workflow_router
from ay_platform_core.observability.workflow.sources import ElasticsearchSpanSource
from tests.fixtures.observability_containers import (
    ElasticsearchEndpoint,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span_summary_doc(
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
) -> dict[str, Any]:
    return {
        "@timestamp": timestamp.isoformat(),
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


def _bulk_index(
    base_url: str,
    *,
    index: str,
    documents: list[dict[str, Any]],
) -> None:
    """Bulk-index `documents` and wait for refresh so they are searchable."""
    body_parts: list[str] = []
    for doc in documents:
        body_parts.append(json.dumps({"index": {"_index": index}}))
        body_parts.append(json.dumps(doc))
    body = "\n".join(body_parts) + "\n"

    resp = httpx.post(
        f"{base_url}/_bulk",
        params={"refresh": "wait_for"},
        content=body,
        headers={"Content-Type": "application/x-ndjson"},
        timeout=30.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"bulk index reported errors: {payload}")


def _create_index_with_keyword_mapping(base_url: str, index: str) -> None:
    """Force a `keyword` mapping on the trace_id field so the adapter's
    `term: trace_id.keyword` filter matches.

    Default dynamic mapping in ES 8 yields `text` + `text.keyword`; the
    adapter relies on the latter, but explicit mapping makes the test
    deterministic across ES dynamic-mapping changes.
    """
    resp = httpx.put(
        f"{base_url}/{index}",
        json={
            "mappings": {
                "properties": {
                    "@timestamp": {"type": "date"},
                    "event": {"type": "keyword"},
                    "trace_id": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                }
            }
        },
        timeout=10.0,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_es_adapter_round_trip_returns_synthesised_envelope(
    elasticsearch_container: ElasticsearchEndpoint,
) -> None:
    index = f"ay-logs-test-{uuid.uuid4().hex[:8]}"
    trace_id = uuid.uuid4().hex
    other_trace = uuid.uuid4().hex
    now = datetime.now(tz=UTC).replace(microsecond=0)

    _create_index_with_keyword_mapping(elasticsearch_container.base_url, index)
    _bulk_index(
        elasticsearch_container.base_url,
        index=index,
        documents=[
            _span_summary_doc(
                trace_id=trace_id,
                span_id="1" * 16,
                component="c2_auth",
                path="/auth/verify",
                timestamp=now - timedelta(seconds=20),
            ),
            _span_summary_doc(
                trace_id=trace_id,
                span_id="2" * 16,
                parent_span_id="1" * 16,
                component="c5_req",
                path="/api/v1/requirements",
                duration_ms=12.5,
                timestamp=now - timedelta(seconds=15),
            ),
            _span_summary_doc(
                trace_id=other_trace,
                span_id="3" * 16,
                component="c4_orchestrator",
                timestamp=now - timedelta(seconds=10),
            ),
        ],
    )

    source = ElasticsearchSpanSource(
        base_url=elasticsearch_container.base_url,
        index=index,
        time_window=timedelta(minutes=10),
        fetch_limit=100,
    )
    try:
        spans = await source.fetch_for_trace(trace_id)
    finally:
        await source.aclose()

    assert {s.span_id for s in spans} == {"1" * 16, "2" * 16}
    assert all(s.trace_id == trace_id for s in spans)
    assert {s.component for s in spans} == {"c2_auth", "c5_req"}


@pytest.mark.asyncio
async def test_es_router_serves_full_envelope(
    elasticsearch_container: ElasticsearchEndpoint,
) -> None:
    """Mount the workflow router on a FastAPI app and exercise the
    end-to-end HTTP path: router -> ES adapter -> ES container."""
    index = f"ay-logs-test-{uuid.uuid4().hex[:8]}"
    trace_id = uuid.uuid4().hex
    now = datetime.now(tz=UTC).replace(microsecond=0)

    _create_index_with_keyword_mapping(elasticsearch_container.base_url, index)
    _bulk_index(
        elasticsearch_container.base_url,
        index=index,
        documents=[
            _span_summary_doc(
                trace_id=trace_id,
                span_id="a" * 16,
                component="c2_auth",
                timestamp=now - timedelta(seconds=8),
            ),
            _span_summary_doc(
                trace_id=trace_id,
                span_id="b" * 16,
                parent_span_id="a" * 16,
                component="c4_orchestrator",
                status_code=500,
                timestamp=now - timedelta(seconds=4),
            ),
        ],
    )

    source = ElasticsearchSpanSource(
        base_url=elasticsearch_container.base_url,
        index=index,
        time_window=timedelta(minutes=10),
    )
    app = FastAPI()
    app.include_router(make_workflow_router(source))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        try:
            resp = await client.get(f"/workflows/{trace_id}")
        finally:
            await source.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["trace_id"] == trace_id
    assert body["summary"]["total_spans"] == 2
    assert body["summary"]["errors"] == 1
    assert body["summary"]["verdict"] == "error"
    assert sorted(body["summary"]["components_touched"]) == [
        "c2_auth",
        "c4_orchestrator",
    ]


@pytest.mark.asyncio
async def test_es_adapter_returns_empty_for_unknown_trace(
    elasticsearch_container: ElasticsearchEndpoint,
) -> None:
    index = f"ay-logs-test-{uuid.uuid4().hex[:8]}"
    _create_index_with_keyword_mapping(elasticsearch_container.base_url, index)
    # No documents indexed.

    source = ElasticsearchSpanSource(
        base_url=elasticsearch_container.base_url,
        index=index,
        time_window=timedelta(minutes=10),
    )
    try:
        spans = await source.fetch_for_trace("a" * 32)
    finally:
        await source.aclose()

    assert spans == []
