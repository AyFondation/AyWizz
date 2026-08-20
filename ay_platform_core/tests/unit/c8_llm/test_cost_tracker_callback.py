# =============================================================================
# File: test_cost_tracker_callback.py
# Version: 2
#
# @relation validates:R-800-070
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
    build_registry_cost_catalog,
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


_REG_PROVIDERS = [
    {"_key": "p-anthropic", "wire_format": "anthropic"},
    {"_key": "p-openai", "wire_format": "openai"},
]
_REG_MODELS = [
    {
        "alias": "flagship",
        "provider_id": "p-anthropic",
        "upstream_model": "claude-opus-4-8",
        "capabilities": {"context_window": 200000, "vision": True},
        "provider_cost_in_per_1m": 5.0,
        "provider_cost_out_per_1m": 25.0,
        "enabled": True,
    },
    {
        "alias": "fast",
        "provider_id": "p-openai",
        "upstream_model": "gpt-5-mini",
        "capabilities": {"context_window": 128000},
        "provider_cost_in_per_1m": 0.3,
        "provider_cost_out_per_1m": 1.2,
        "enabled": True,
    },
]


@pytest.mark.unit
class TestRegistryCostCatalog:
    """Bloc 5 (D-011): cost comes from the REGISTRY, keyed by the resolved
    `<wire>/<upstream>` model — exactly `envelope.model` after the client
    rewrite — with the operator-set per-model provider_cost."""

    def test_keys_by_resolved_model_with_registry_costs(self) -> None:
        cat = build_registry_cost_catalog(_REG_MODELS, _REG_PROVIDERS)
        # Keyed by `<wire>/<upstream>` (== envelope.model), NOT the alias.
        assert set(cat.keys()) == {"anthropic/claude-opus-4-8", "openai/gpt-5-mini"}
        anth = cat["anthropic/claude-opus-4-8"]
        assert anth.cost_per_million_input == 5.0
        assert anth.cost_per_million_output == 25.0
        assert anth.context_window == 200000

    def test_provider_independent_any_wire_format(self) -> None:
        # An OpenAI-family model is catalogued identically — no Anthropic bias.
        assert "openai/gpt-5-mini" in build_registry_cost_catalog(
            _REG_MODELS, _REG_PROVIDERS
        )

    def test_disabled_models_are_excluded(self) -> None:
        models = [{**_REG_MODELS[0], "enabled": False}]
        assert build_registry_cost_catalog(models, _REG_PROVIDERS) == {}

    def test_model_without_provider_is_skipped(self) -> None:
        models = [{**_REG_MODELS[0], "provider_id": "missing"}]
        assert build_registry_cost_catalog(models, _REG_PROVIDERS) == {}

    def test_empty_registry_yields_empty_catalog(self) -> None:
        assert build_registry_cost_catalog([], []) == {}
