# =============================================================================
# File: test_sources_loki.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/workflow/test_sources_loki.py
# Description: Unit tests for LokiSpanSource. Uses `httpx.MockTransport` to
#              return canned `query_range` payloads; asserts on the
#              outbound URL/params/LogQL AND on the parsed Span list.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ay_platform_core.observability.workflow import sources as src_mod
from ay_platform_core.observability.workflow.config import WorkflowSourceSettings
from ay_platform_core.observability.workflow.sources import LokiSpanSource

from ._fixtures import make_span_summary_line

pytestmark = pytest.mark.unit

_FIXED_NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `_now_utc` so we can assert exact `start`/`end` query params."""
    monkeypatch.setattr(src_mod, "_now_utc", lambda: _FIXED_NOW)


def _loki_payload(lines: list[str]) -> dict[str, object]:
    """Wrap raw log lines in the Loki `query_range` response envelope."""
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"container": "ay-c2_auth"},
                    "values": [
                        # Loki returns [ns_string, line]; the timestamp is
                        # not used by our parser (we re-extract from the
                        # JSON line itself).
                        ["1714214400000000000", line]
                        for line in lines
                    ],
                }
            ],
        },
    }


def _build_source(
    handler: httpx.MockTransport,
    *,
    base_url: str = "http://loki:3100",
    label_selector: str = '{container=~"ay-.*"}',
    time_window: timedelta = timedelta(hours=1),
    fetch_limit: int = 5_000,
) -> LokiSpanSource:
    client = httpx.AsyncClient(transport=handler)
    return LokiSpanSource(
        base_url=base_url,
        label_selector=label_selector,
        time_window=time_window,
        fetch_limit=fetch_limit,
        client=client,
    )


# ---------------------------------------------------------------------------
# fetch_for_trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_for_trace_emits_correct_logql_and_time_bounds() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_loki_payload([]))

    source = _build_source(httpx.MockTransport(handler))
    spans = await source.fetch_for_trace("d" * 32)
    await source.aclose()

    assert spans == []
    params = captured["params"]
    assert isinstance(params, dict)
    query = params["query"]
    assert isinstance(query, str)
    # LogQL pipeline: stream selector + json parser + event filter +
    # trace_id filter. We use `| json | event="span_summary"` rather
    # than a substring `|=` filter so whitespace in the JSON line
    # (e.g. `"event": "span_summary"` with a space) doesn't defeat us.
    assert '{container=~"ay-.*"}' in query
    assert "| json" in query
    assert 'event="span_summary"' in query
    assert f'trace_id="{"d" * 32}"' in query
    # Time window = 1h; end = _FIXED_NOW; start = end - 1h.
    end_ns = int(_FIXED_NOW.timestamp() * 1_000_000_000)
    start_ns = int((_FIXED_NOW - timedelta(hours=1)).timestamp() * 1_000_000_000)
    assert params["end"] == str(end_ns)
    assert params["start"] == str(start_ns)
    assert params["direction"] == "backward"


@pytest.mark.asyncio
async def test_fetch_for_trace_parses_matching_lines() -> None:
    target_trace = "c" * 32
    other_trace = "f" * 32
    payload_lines = [
        make_span_summary_line(trace_id=target_trace, span_id="a" * 16),
        make_span_summary_line(trace_id=target_trace, span_id="b" * 16),
        # Loki's substring filter would normally exclude this, but the
        # parser still has to be tolerant if the upstream returns extras.
        make_span_summary_line(trace_id=other_trace, span_id="c" * 16),
        # Non-span_summary line silently dropped by the parser.
        '{"event":"other_event","trace_id":"' + target_trace + '"}',
        # Garbage line — parser is lenient, drops without raising.
        "this is not json",
    ]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_loki_payload(payload_lines))

    source = _build_source(httpx.MockTransport(handler))
    spans = await source.fetch_for_trace(target_trace)
    await source.aclose()

    # The adapter does NOT post-filter by trace_id (it trusts Loki's
    # substring filter). All three span_summary lines are returned;
    # callers (router) re-group via group_by_trace.
    assert len(spans) == 3
    assert {s.span_id for s in spans} == {"a" * 16, "b" * 16, "c" * 16}


@pytest.mark.asyncio
async def test_fetch_for_trace_raises_on_loki_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="loki unavailable")

    source = _build_source(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await source.fetch_for_trace("a" * 32)
    await source.aclose()


# ---------------------------------------------------------------------------
# fetch_recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_recent_omits_trace_filter() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.params.get("query", "")
        return httpx.Response(200, json=_loki_payload([]))

    source = _build_source(httpx.MockTransport(handler))
    await source.fetch_recent(limit=42)
    await source.aclose()

    query = captured["query"]
    assert isinstance(query, str)
    assert 'event="span_summary"' in query
    # `trace_id="..."` filter MUST NOT appear on the recent endpoint;
    # the field name itself can appear via `| json` extraction (it's
    # extracted from every line) but no equality predicate on it.
    assert 'trace_id="' not in query


@pytest.mark.asyncio
async def test_fetch_recent_uses_since_when_provided() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_loki_payload([]))

    since = datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC)
    source = _build_source(httpx.MockTransport(handler))
    await source.fetch_recent(since=since, limit=10)
    await source.aclose()

    expected_start_ns = int(since.timestamp() * 1_000_000_000)
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["start"] == str(expected_start_ns)
    assert params["limit"] == "10"


# ---------------------------------------------------------------------------
# from_settings + lifecycle
# ---------------------------------------------------------------------------


def test_from_settings_propagates_config() -> None:
    settings = WorkflowSourceSettings(
        span_source="loki",
        loki_url="http://loki.example:3100",
        loki_label_selector='{namespace="ay"}',
        query_window_hours=2.0,
        fetch_limit=42,
        request_timeout_seconds=3.0,
    )
    source = LokiSpanSource.from_settings(settings)
    assert source._base_url == "http://loki.example:3100"
    assert source._label_selector == '{namespace="ay"}'
    assert source._time_window == timedelta(hours=2.0)
    assert source._fetch_limit == 42


@pytest.mark.asyncio
async def test_aclose_is_idempotent_and_only_closes_owned_clients() -> None:
    # Source owns the client (none injected): aclose closes it.
    owned = LokiSpanSource(base_url="http://loki:3100")
    await owned.aclose()
    await owned.aclose()  # idempotent — must not raise.

    # Source does NOT own the client: aclose leaves the user's client open.
    user_client = httpx.AsyncClient()
    borrowed = LokiSpanSource(base_url="http://loki:3100", client=user_client)
    await borrowed.aclose()
    assert not user_client.is_closed
    await user_client.aclose()
