# =============================================================================
# File: test_cost_tracker_callback.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_cost_tracker_callback.py
# Description: Unit tests — the cost-tracker LiteLLM callback extracts tags
#              from request headers, applies the normative cost formula,
#              and emits a CallRecord matching E-800-002.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ay_platform_core.c8_llm.callbacks.cost_tracker import (
    CostTrackerCallback,
    _extract_tags,
    _fingerprint,
    _provider_of,
)
from ay_platform_core.c8_llm.catalog import Feature
from ay_platform_core.c8_llm.config import ModelInfo
from ay_platform_core.c8_llm.models import CallRecord


class _InMemorySink:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def insert(self, record: CallRecord) -> None:
        self.records.append(record)


def _model_info() -> ModelInfo:
    return ModelInfo(
        display_name="Sonnet",
        features=[Feature.CHAT_COMPLETION, Feature.STREAMING],
        context_window=200_000,
        cost_per_million_input=3.0,
        cost_per_million_output=15.0,
    )


_SAMPLE_HEADERS = {
    "X-Tenant-Id": "t-1",
    "X-Project-Id": "p-1",
    "X-User-Id": "u-1",
    "X-Session-Id": "s-1",
    "X-Agent-Name": "planner",
    "X-Phase": "plan",
}


@pytest.mark.unit
class TestExtractTags:
    def test_reads_proxy_server_request_headers(self) -> None:
        tags = _extract_tags({"proxy_server_request": {"headers": _SAMPLE_HEADERS}})
        assert tags.tenant_id == "t-1"
        assert tags.agent_name == "planner"
        assert tags.phase == "plan"

    def test_reads_metadata_fallback(self) -> None:
        tags = _extract_tags({
            "metadata": {
                "tenant_id": "t-9",
                "session_id": "s-9",
                "agent_name": "architect",
            },
        })
        assert tags.tenant_id == "t-9"
        assert tags.session_id == "s-9"
        assert tags.agent_name == "architect"

    def test_missing_mandatory_defaults_to_unknown(self) -> None:
        tags = _extract_tags({})
        assert tags.tenant_id == "unknown"
        assert tags.session_id == "unknown"
        assert tags.agent_name == "unknown"


@pytest.mark.unit
class TestProviderOf:
    def test_provider_prefix(self) -> None:
        assert _provider_of("anthropic/claude-sonnet-4-6") == "anthropic"

    def test_claude_alias(self) -> None:
        assert _provider_of("claude-opus-flagship") == "anthropic"

    def test_gpt_alias(self) -> None:
        assert _provider_of("gpt-5") == "openai"

    def test_unknown_returns_unknown(self) -> None:
        assert _provider_of("some-local-model") == "unknown"


@pytest.mark.unit
class TestFingerprint:
    def test_deterministic_for_same_input(self) -> None:
        req = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        assert _fingerprint(req) == _fingerprint(req)

    def test_ignores_non_semantic_fields(self) -> None:
        a = {"model": "m", "messages": [], "stream": True, "user_tag": "alice"}
        b = {"model": "m", "messages": [], "stream": False, "user_tag": "bob"}
        # `stream` and `user_tag` are not part of the fingerprint projection
        assert _fingerprint(a) == _fingerprint(b)

    def test_different_messages_yield_different_fingerprints(self) -> None:
        a = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        b = {"model": "m", "messages": [{"role": "user", "content": "bye"}]}
        assert _fingerprint(a) != _fingerprint(b)

    def test_has_sha256_prefix(self) -> None:
        assert _fingerprint({}).startswith("sha256:")


@pytest.mark.unit
@pytest.mark.asyncio
class TestCallback:
    async def test_successful_call_records_cost(self) -> None:
        sink = _InMemorySink()
        callback = CostTrackerCallback(sink, {"sonnet": _model_info()})
        start = datetime.now(UTC)
        end = start + timedelta(milliseconds=1500)
        await callback.handle_post_call(
            request_data={
                "model": "sonnet",
                "messages": [{"role": "user", "content": "hi"}],
                "proxy_server_request": {"headers": _SAMPLE_HEADERS},
            },
            response={
                "model": "sonnet",
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "total_tokens": 1500,
                    "cached_tokens": 200,
                },
            },
            start_time=start,
            end_time=end,
        )
        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.model == "sonnet"
        assert record.status == "success"
        assert record.input_tokens == 1000
        assert record.output_tokens == 500
        assert record.cached_tokens == 200
        assert record.latency_ms == 1500
        assert record.tags.agent_name == "planner"
        # Spot check: cost > 0 with our model info
        assert record.cost_usd > 0.0

    async def test_failure_handler_records_error(self) -> None:
        sink = _InMemorySink()
        callback = CostTrackerCallback(sink, {})
        start = datetime.now(UTC)
        end = start + timedelta(milliseconds=200)
        await callback.handle_failure(
            request_data={
                "model": "claude-haiku-fast",
                "proxy_server_request": {"headers": _SAMPLE_HEADERS},
            },
            error=TimeoutError("provider timed out"),
            start_time=start,
            end_time=end,
        )
        assert len(sink.records) == 1
        record = sink.records[0]
        assert record.status == "failure"
        assert record.error_code == "TimeoutError"
        assert "timed out" in (record.error_message or "")
        assert record.cost_usd == 0.0
