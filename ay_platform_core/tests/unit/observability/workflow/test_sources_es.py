# =============================================================================
# File: test_sources_es.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/workflow/test_sources_es.py
# Description: Unit tests for ElasticsearchSpanSource. Uses
#              `httpx.MockTransport` to return canned `_search` payloads;
#              asserts on the outbound URL, request body, and parsed
#              Span list.
# =============================================================================

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ay_platform_core.observability.workflow import sources as src_mod
from ay_platform_core.observability.workflow.config import WorkflowSourceSettings
from ay_platform_core.observability.workflow.sources import ElasticsearchSpanSource

from ._fixtures import make_span_summary

pytestmark = pytest.mark.unit

_FIXED_NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(src_mod, "_now_utc", lambda: _FIXED_NOW)


def _es_payload(sources: list[dict[str, object]]) -> dict[str, object]:
    """Wrap a list of `_source` dicts in the `_search` response envelope."""
    return {
        "took": 1,
        "timed_out": False,
        "hits": {
            "total": {"value": len(sources), "relation": "eq"},
            "max_score": None,
            "hits": [
                {
                    "_index": "ay-logs-2026.04.27",
                    "_id": str(i),
                    "_score": None,
                    "_source": src,
                }
                for i, src in enumerate(sources)
            ],
        },
    }


def _build_source(
    handler: httpx.MockTransport,
    *,
    base_url: str = "http://elasticsearch:9200",
    index: str = "ay-logs-*",
    fetch_limit: int = 5_000,
    username: str = "",
    password: str = "",
) -> ElasticsearchSpanSource:
    client = httpx.AsyncClient(transport=handler)
    return ElasticsearchSpanSource(
        base_url=base_url,
        index=index,
        fetch_limit=fetch_limit,
        username=username,
        password=password,
        client=client,
    )


# ---------------------------------------------------------------------------
# fetch_for_trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_for_trace_emits_correct_query_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_es_payload([]))

    source = _build_source(httpx.MockTransport(handler))
    spans = await source.fetch_for_trace("d" * 32)
    await source.aclose()

    assert spans == []
    url = captured["url"]
    assert isinstance(url, str)
    assert url.endswith("/ay-logs-*/_search")

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["size"] == 5_000
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"event": "span_summary"}} in filters
    assert {"term": {"trace_id.keyword": "d" * 32}} in filters
    # Range filter on @timestamp is included.
    range_filters = [f for f in filters if "range" in f]
    assert len(range_filters) == 1
    assert "gte" in range_filters[0]["range"]["@timestamp"]
    assert "lte" in range_filters[0]["range"]["@timestamp"]
    # Sort ascending by @timestamp for deterministic ordering.
    assert body["sort"] == [{"@timestamp": {"order": "asc"}}]


@pytest.mark.asyncio
async def test_fetch_for_trace_parses_source_documents_directly() -> None:
    target_trace = "c" * 32
    sources = [
        make_span_summary(trace_id=target_trace, span_id="a" * 16, component="c2_auth"),
        make_span_summary(trace_id=target_trace, span_id="b" * 16, component="c5_req"),
        # Document missing the event marker is silently dropped.
        {"trace_id": target_trace, "span_id": "x", "event": "other"},
        # Bare wrong-shape document — span_from_dict returns None.
        {"trace_id": target_trace, "event": "span_summary"},
    ]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_es_payload(sources))

    source = _build_source(httpx.MockTransport(handler))
    spans = await source.fetch_for_trace(target_trace)
    await source.aclose()

    # Only the two valid span_summary documents are returned; the
    # filtering decision (drop the malformed third doc which lacks
    # `timestamp`) is the parser's, not the adapter's.
    assert len(spans) == 2
    assert {s.span_id for s in spans} == {"a" * 16, "b" * 16}
    assert {s.component for s in spans} == {"c2_auth", "c5_req"}


@pytest.mark.asyncio
async def test_fetch_for_trace_raises_on_es_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="cluster_block_exception")

    source = _build_source(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await source.fetch_for_trace("a" * 32)
    await source.aclose()


# ---------------------------------------------------------------------------
# fetch_recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_recent_omits_trace_filter_and_uses_size_limit() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_es_payload([]))

    source = _build_source(httpx.MockTransport(handler))
    await source.fetch_recent(limit=42)
    await source.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["size"] == 42
    filters = body["query"]["bool"]["filter"]
    # No trace_id filter on a generic recent query.
    assert all("trace_id.keyword" not in (f.get("term") or {}) for f in filters)


@pytest.mark.asyncio
async def test_fetch_recent_uses_since_for_range_floor() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_es_payload([]))

    since = datetime(2026, 4, 27, 10, 30, 0, tzinfo=UTC)
    source = _build_source(httpx.MockTransport(handler))
    await source.fetch_recent(since=since, limit=10)
    await source.aclose()

    body = captured["body"]
    assert isinstance(body, dict)
    range_filters = [f for f in body["query"]["bool"]["filter"] if "range" in f]
    assert len(range_filters) == 1
    assert range_filters[0]["range"]["@timestamp"]["gte"] == since.isoformat()


# ---------------------------------------------------------------------------
# Auth + lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_auth_is_sent_when_credentials_set() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_es_payload([]))

    source = _build_source(
        httpx.MockTransport(handler),
        username="elastic",
        password="changeme",
    )
    await source.fetch_recent(limit=1)
    await source.aclose()

    auth = captured["authorization"]
    assert isinstance(auth, str)
    assert auth.startswith("Basic ")


@pytest.mark.asyncio
async def test_no_auth_header_when_credentials_empty() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_es_payload([]))

    source = _build_source(httpx.MockTransport(handler))
    await source.fetch_recent(limit=1)
    await source.aclose()

    assert captured["authorization"] == ""


def test_from_settings_propagates_config() -> None:
    settings = WorkflowSourceSettings(
        span_source="elasticsearch",
        elasticsearch_url="http://es.example:9200",
        elasticsearch_index="ay-prod-*",
        elasticsearch_username="u",
        elasticsearch_password="p",
        query_window_hours=6.0,
        fetch_limit=99,
    )
    source = ElasticsearchSpanSource.from_settings(settings)
    assert source._base_url == "http://es.example:9200"
    assert source._index == "ay-prod-*"
    assert source._fetch_limit == 99
    assert source._auth == ("u", "p")
    assert source._time_window == timedelta(hours=6.0)


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    source = ElasticsearchSpanSource(base_url="http://elasticsearch:9200")
    await source.aclose()
    await source.aclose()  # idempotent
