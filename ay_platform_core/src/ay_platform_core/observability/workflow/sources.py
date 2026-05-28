# =============================================================================
# File: sources.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/workflow/sources.py
# Description: `SpanSource` Protocol and three concrete adapters:
#                - `BufferSpanSource` (in-process LogRingBuffer, test-tier)
#                - `LokiSpanSource` (Grafana Loki HTTP API)
#                - `ElasticsearchSpanSource` (Elasticsearch HTTP API)
#
#              Adapters are async, accept an injected `httpx.AsyncClient`
#              for testability, and own no global state. The synthesis
#              algorithm consumes their output identically.
#
# @relation implements:R-100-124
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx

from ay_platform_core._observability.synthesis import (
    Span,
    parse_lines,
    parse_span_summary,
    span_from_dict,
)

if TYPE_CHECKING:
    from ay_platform_core._observability.buffer import LogRingBuffer
    from ay_platform_core.observability.workflow.config import WorkflowSourceSettings


@runtime_checkable
class SpanSource(Protocol):
    """Abstract source of `span_summary` records.

    Implementations may pull from a local buffer, Loki, Elasticsearch,
    or any other backend that persists structured logs. They return
    parsed `Span` objects ready for `synthesise_workflow` /
    `list_recent_traces` (see `_observability/synthesis.py`).

    All methods are async to accommodate HTTP-bound adapters; in-memory
    adapters fulfil the contract trivially.
    """

    async def fetch_for_trace(self, trace_id: str) -> list[Span]:
        """Return every span belonging to `trace_id`, in arbitrary order."""
        ...

    async def fetch_recent(
        self,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Span]:
        """Return the most recent spans, regardless of trace.

        `since` is an inclusive lower bound on the span's `timestamp`;
        `limit` caps the number of records pulled (the synthesis layer
        may then group + take the top N traces).
        """
        ...

    async def aclose(self) -> None:
        """Release any underlying HTTP resources. Idempotent."""
        ...


# ---------------------------------------------------------------------------
# Buffer adapter — in-process, test-tier
# ---------------------------------------------------------------------------


class BufferSpanSource:
    """Adapter over the test-tier `_observability` LogRingBuffer.

    Lives in the same process as the buffer; reads cost a single
    in-memory snapshot. Used by the test-tier collector and by tests
    that exercise the synthesis API without standing up Loki / ES.
    """

    def __init__(self, buffer: LogRingBuffer) -> None:
        self._buffer = buffer

    async def fetch_for_trace(self, trace_id: str) -> list[Span]:
        entries = self._buffer.tail(limit=100_000)
        spans = parse_lines(entry.line for entry in entries)
        return [s for s in spans if s.trace_id == trace_id]

    async def fetch_recent(
        self,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Span]:
        entries = self._buffer.tail(since=since, limit=limit)
        return parse_lines(entry.line for entry in entries)

    async def aclose(self) -> None:
        # Buffer lifecycle is owned by the test-tier app; nothing to do.
        return None


# ---------------------------------------------------------------------------
# Loki adapter — HTTP, production
# ---------------------------------------------------------------------------

# LogQL pipeline. We pipe through `| json` so the line filter can use
# extracted structured fields rather than substring matches — the
# platform's JSONFormatter writes `"event": "span_summary"` with a
# space after the colon, which would defeat a naive `|=` substring
# filter. The `| json` parser tolerates whitespace.
_LOKI_SPAN_SUMMARY_PIPELINE = '| json | event="span_summary"'


def _now_utc() -> datetime:
    # Indirection so tests can monkeypatch `_now_utc` to a fixed instant
    # and assert deterministic time bounds in the upstream query string.
    return datetime.now(tz=UTC)


class LokiSpanSource:
    """Grafana Loki HTTP adapter.

    Queries `GET /loki/api/v1/query_range` with a LogQL stream selector
    and a substring filter for `event=span_summary`. Each matched log
    line is JSON-decoded into a Span via `parse_span_summary`.

    Reference: https://grafana.com/docs/loki/latest/reference/api/
    """

    def __init__(
        self,
        *,
        base_url: str,
        label_selector: str = '{container=~"ay-.*"}',
        time_window: timedelta = timedelta(hours=24),
        fetch_limit: int = 5_000,
        client: httpx.AsyncClient | None = None,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._label_selector = label_selector
        self._time_window = time_window
        self._fetch_limit = fetch_limit
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=request_timeout_seconds)

    @classmethod
    def from_settings(
        cls,
        settings: WorkflowSourceSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> LokiSpanSource:
        return cls(
            base_url=settings.loki_url,
            label_selector=settings.loki_label_selector,
            time_window=timedelta(hours=settings.query_window_hours),
            fetch_limit=settings.fetch_limit,
            request_timeout_seconds=settings.request_timeout_seconds,
            client=client,
        )

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch_for_trace(self, trace_id: str) -> list[Span]:
        # `| json` extracts `event` and `trace_id` so we can filter on
        # them by equality regardless of the original line's whitespace.
        query = (
            f"{self._label_selector} {_LOKI_SPAN_SUMMARY_PIPELINE} "
            f'| trace_id="{trace_id}"'
        )
        return await self._query(query)

    async def fetch_recent(
        self,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Span]:
        query = f"{self._label_selector} {_LOKI_SPAN_SUMMARY_PIPELINE}"
        return await self._query(query, since=since, limit=limit)

    async def _query(
        self,
        logql: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Span]:
        end = _now_utc()
        start = since or (end - self._time_window)
        params = {
            "query": logql,
            "start": str(_to_unix_nanos(start)),
            "end": str(_to_unix_nanos(end)),
            "limit": str(limit if limit is not None else self._fetch_limit),
            "direction": "backward",
        }
        response = await self._client.get(
            f"{self._base_url}/loki/api/v1/query_range",
            params=params,
        )
        response.raise_for_status()
        return _parse_loki_response(response.json())


def _to_unix_nanos(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def _parse_loki_response(payload: Any) -> list[Span]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    result = data.get("result") or []
    if not isinstance(result, list):
        return []
    out: list[Span] = []
    for stream in result:
        values = stream.get("values") if isinstance(stream, dict) else None
        if not isinstance(values, list):
            continue
        for entry in values:
            # Each entry is `[ts_nanoseconds_string, log_line]`.
            if not (isinstance(entry, list) and len(entry) >= 2):
                continue
            line = entry[1]
            if not isinstance(line, str):
                continue
            span = parse_span_summary(line)
            if span is not None:
                out.append(span)
    return out


# ---------------------------------------------------------------------------
# Elasticsearch adapter — HTTP, production
# ---------------------------------------------------------------------------


class ElasticsearchSpanSource:
    """Elasticsearch HTTP adapter.

    Queries `POST /<index>/_search` with a bool filter on
    `event=span_summary` (+ optional trace_id filter). Documents are
    expected to mirror the JSON shape emitted by the platform formatter
    (R-100-104), so each `_source` is parsed via `span_from_dict`
    directly — no re-serialisation through the JSON line path.

    Reference:
    https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-search
    """

    def __init__(
        self,
        *,
        base_url: str,
        index: str = "ay-logs-*",
        time_window: timedelta = timedelta(hours=24),
        fetch_limit: int = 5_000,
        username: str = "",
        password: str = "",
        client: httpx.AsyncClient | None = None,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._index = index
        self._time_window = time_window
        self._fetch_limit = fetch_limit
        self._auth: tuple[str, str] | None = (
            (username, password) if username and password else None
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=request_timeout_seconds)

    @classmethod
    def from_settings(
        cls,
        settings: WorkflowSourceSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> ElasticsearchSpanSource:
        return cls(
            base_url=settings.elasticsearch_url,
            index=settings.elasticsearch_index,
            time_window=timedelta(hours=settings.query_window_hours),
            fetch_limit=settings.fetch_limit,
            username=settings.elasticsearch_username,
            password=settings.elasticsearch_password,
            request_timeout_seconds=settings.request_timeout_seconds,
            client=client,
        )

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch_for_trace(self, trace_id: str) -> list[Span]:
        body = self._build_body(trace_id=trace_id, since=None, size=self._fetch_limit)
        return await self._search(body)

    async def fetch_recent(
        self,
        *,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[Span]:
        body = self._build_body(trace_id=None, since=since, size=limit)
        return await self._search(body)

    def _build_body(
        self,
        *,
        trace_id: str | None,
        since: datetime | None,
        size: int,
    ) -> dict[str, Any]:
        end = _now_utc()
        start = since or (end - self._time_window)
        filters: list[dict[str, Any]] = [
            # Plain `term` rather than `term.keyword`: the platform's JSON
            # logs are typically ingested with dynamic mapping that yields
            # both `event` and `event.keyword`; using the raw field works
            # against either; we add the `keyword` form on the trace_id
            # filter where exact-match semantics matter most.
            {"term": {"event": "span_summary"}},
            {
                "range": {
                    "@timestamp": {
                        "gte": start.isoformat(),
                        "lte": end.isoformat(),
                    }
                }
            },
        ]
        if trace_id:
            filters.append({"term": {"trace_id.keyword": trace_id}})
        return {
            "size": size,
            "sort": [{"@timestamp": {"order": "asc"}}],
            "query": {"bool": {"filter": filters}},
        }

    async def _search(self, body: dict[str, Any]) -> list[Span]:
        url = f"{self._base_url}/{self._index}/_search"
        # `auth=None` would override the AsyncClient's default; only pass
        # auth when explicitly configured.
        if self._auth is None:
            response = await self._client.post(url, json=body)
        else:
            response = await self._client.post(url, json=body, auth=self._auth)
        response.raise_for_status()
        return _parse_elasticsearch_response(response.json())


def _parse_elasticsearch_response(payload: Any) -> list[Span]:
    if not isinstance(payload, dict):
        return []
    hits_outer = payload.get("hits")
    if not isinstance(hits_outer, dict):
        return []
    hits = hits_outer.get("hits")
    if not isinstance(hits, list):
        return []
    out: list[Span] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        source = hit.get("_source")
        span = span_from_dict(source)
        if span is not None:
            out.append(span)
    return out


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_span_source(
    settings: WorkflowSourceSettings,
    *,
    buffer: LogRingBuffer | None = None,
    client: httpx.AsyncClient | None = None,
) -> SpanSource:
    """Build the configured `SpanSource` from settings.

    `buffer` is required iff `settings.span_source == "buffer"` — the
    in-process buffer is owned by whoever creates the source. `client`
    is optional and only consumed by HTTP adapters (Loki, ES); when
    omitted, each adapter creates and owns its own AsyncClient.
    """
    if settings.span_source == "buffer":
        if buffer is None:
            raise ValueError(
                "BufferSpanSource requires a LogRingBuffer; "
                "set OBS_SPAN_SOURCE=loki|elasticsearch or pass buffer=...",
            )
        return BufferSpanSource(buffer)
    if settings.span_source == "loki":
        return LokiSpanSource.from_settings(settings, client=client)
    if settings.span_source == "elasticsearch":
        return ElasticsearchSpanSource.from_settings(settings, client=client)
    raise ValueError(f"unknown span_source: {settings.span_source!r}")


# Re-export so callers can `from ...workflow.sources import Span` if they
# want the dataclass without reaching into the test-tier package.
__all__ = [
    "BufferSpanSource",
    "ElasticsearchSpanSource",
    "LokiSpanSource",
    "Span",
    "SpanSource",
    "create_span_source",
]
