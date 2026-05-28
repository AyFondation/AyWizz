# =============================================================================
# File: test_sources_buffer_and_factory.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/workflow/test_sources_buffer_and_factory.py
# Description: BufferSpanSource (in-process) + create_span_source factory.
#              These cover the test-tier path and the dispatch logic
#              that picks the right adapter from settings.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ay_platform_core._observability.buffer import LogEntry, LogRingBuffer
from ay_platform_core.observability.workflow.config import WorkflowSourceSettings
from ay_platform_core.observability.workflow.sources import (
    BufferSpanSource,
    ElasticsearchSpanSource,
    LokiSpanSource,
    create_span_source,
)

from ._fixtures import make_span_summary_line

pytestmark = pytest.mark.unit


def _seed_buffer(buffer: LogRingBuffer, lines: list[tuple[str, str]]) -> None:
    """Seed the buffer with `(service, line)` pairs."""
    for service, line in lines:
        buffer.append(
            LogEntry(
                service=service,
                timestamp=datetime.now(tz=UTC),
                severity="INFO",
                line=line,
            )
        )


# ---------------------------------------------------------------------------
# BufferSpanSource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buffer_fetch_for_trace_returns_only_matching_trace() -> None:
    buffer = LogRingBuffer(max_per_service=1000)
    target = "c" * 32
    other = "f" * 32
    _seed_buffer(
        buffer,
        [
            ("ay-c2_auth", make_span_summary_line(trace_id=target, span_id="a" * 16)),
            ("ay-c5_req", make_span_summary_line(trace_id=target, span_id="b" * 16)),
            ("ay-c5_req", make_span_summary_line(trace_id=other, span_id="c" * 16)),
            # Non-span_summary line, should be dropped by the parser.
            ("ay-c2_auth", "not a span summary"),
        ],
    )

    source = BufferSpanSource(buffer)
    spans = await source.fetch_for_trace(target)
    await source.aclose()

    assert {s.span_id for s in spans} == {"a" * 16, "b" * 16}


@pytest.mark.asyncio
async def test_buffer_fetch_recent_passes_limit_and_since() -> None:
    buffer = LogRingBuffer(max_per_service=1000)
    _seed_buffer(
        buffer,
        [
            ("ay-c2_auth", make_span_summary_line(span_id="a" * 16)),
            ("ay-c2_auth", make_span_summary_line(span_id="b" * 16)),
            ("ay-c2_auth", make_span_summary_line(span_id="c" * 16)),
        ],
    )

    source = BufferSpanSource(buffer)
    spans = await source.fetch_recent(limit=2)
    await source.aclose()

    # The buffer's `tail(limit=2)` returns the two most recent entries.
    assert len(spans) == 2


@pytest.mark.asyncio
async def test_buffer_aclose_is_a_noop() -> None:
    buffer = LogRingBuffer(max_per_service=10)
    source = BufferSpanSource(buffer)
    await source.aclose()
    await source.aclose()  # idempotent


# ---------------------------------------------------------------------------
# create_span_source factory
# ---------------------------------------------------------------------------


def test_factory_buffer_requires_buffer_argument() -> None:
    settings = WorkflowSourceSettings(span_source="buffer")
    with pytest.raises(ValueError, match="LogRingBuffer"):
        create_span_source(settings)


def test_factory_buffer_returns_buffer_source() -> None:
    settings = WorkflowSourceSettings(span_source="buffer")
    buffer = LogRingBuffer(max_per_service=10)
    source = create_span_source(settings, buffer=buffer)
    assert isinstance(source, BufferSpanSource)


def test_factory_loki_returns_loki_source() -> None:
    settings = WorkflowSourceSettings(
        span_source="loki",
        loki_url="http://loki.example:3100",
    )
    source = create_span_source(settings)
    assert isinstance(source, LokiSpanSource)


def test_factory_elasticsearch_returns_es_source() -> None:
    settings = WorkflowSourceSettings(
        span_source="elasticsearch",
        elasticsearch_url="http://es.example:9200",
    )
    source = create_span_source(settings)
    assert isinstance(source, ElasticsearchSpanSource)


# ---------------------------------------------------------------------------
# Settings env precedence (sanity check)
# ---------------------------------------------------------------------------


def test_settings_pull_from_env_with_obs_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBS_SPAN_SOURCE", "loki")
    monkeypatch.setenv("OBS_LOKI_URL", "http://loki.test:3100")
    monkeypatch.setenv("OBS_QUERY_WINDOW_HOURS", "12")
    monkeypatch.setenv("OBS_FETCH_LIMIT", "777")

    settings = WorkflowSourceSettings()
    assert settings.span_source == "loki"
    assert settings.loki_url == "http://loki.test:3100"
    assert settings.query_window_hours == 12.0
    assert settings.fetch_limit == 777
