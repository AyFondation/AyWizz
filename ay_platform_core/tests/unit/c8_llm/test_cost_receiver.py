# =============================================================================
# File: test_cost_receiver.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_cost_receiver.py
# Description: Unit tests for the C8 cost receiver's pure core
#              (`build_call_record`) and catalog loader (`_load_catalog`),
#              R-800-070. Validates cost from the model catalog, graceful
#              zero-cost when a model is absent, header→tag projection,
#              the cached-token clamp, and failure-status mapping.
#
# @relation validates:R-800-070
# =============================================================================

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ay_platform_core.c8_llm.callbacks.cost_tracker import build_call_record
from ay_platform_core.c8_llm.config import ModelInfo
from ay_platform_core.c8_llm.main import _load_catalog
from ay_platform_core.c8_llm.models import CostCallEnvelope

pytestmark = pytest.mark.unit


def _info(input_rate: float, output_rate: float) -> ModelInfo:
    # model_validate so the "chat_completion" string coerces to the
    # Feature literal (the constructor would type-check it as a bare str).
    return ModelInfo.model_validate(
        {
            "display_name": "x",
            "features": ["chat_completion"],
            "context_window": 1000,
            "cost_per_million_input": input_rate,
            "cost_per_million_output": output_rate,
        },
    )


def _env(**overrides: Any) -> CostCallEnvelope:
    base: dict[str, Any] = {
        "status": "success",
        "model": "claude-haiku-fast",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cached_tokens": 0},
        "headers": {
            "X-Agent-Name": "c3-docgen",
            "X-Tenant-Id": "t1",
            "X-Session-Id": "s1",
        },
        "fingerprint": "sha256:abc",
        "start_time": "2026-05-22T10:00:00+00:00",
        "end_time": "2026-05-22T10:00:02+00:00",
    }
    base.update(overrides)
    return CostCallEnvelope(**base)


class TestBuildCallRecord:
    def test_cost_computed_from_catalog(self) -> None:
        rec = build_call_record(_env(), {"claude-haiku-fast": _info(0.8, 4.0)})
        # 1000 in @ 0.8/M = 0.0008 ; 500 out @ 4.0/M = 0.002 → 0.0028.
        assert rec.cost_usd == pytest.approx(0.0028)
        assert rec.model == "claude-haiku-fast"
        assert rec.provider == "anthropic"
        assert rec.input_tokens == 1000
        assert rec.output_tokens == 500
        assert rec.latency_ms == 2000
        assert rec.request_fingerprint == "sha256:abc"
        assert rec.status == "success"

    def test_tags_projected_from_headers(self) -> None:
        rec = build_call_record(_env(), {})
        assert rec.tags.agent_name == "c3-docgen"
        assert rec.tags.tenant_id == "t1"
        assert rec.tags.session_id == "s1"

    def test_cost_zero_when_model_absent_from_catalog(self) -> None:
        # Graceful : the call is still recorded ; cost just stays 0.
        rec = build_call_record(_env(), {})
        assert rec.cost_usd == 0.0

    def test_cached_tokens_clamped_to_input(self) -> None:
        rec = build_call_record(
            _env(usage={"prompt_tokens": 100, "completion_tokens": 0, "cached_tokens": 999}),
            {"claude-haiku-fast": _info(0.8, 4.0)},
        )
        # Clamped so the compute_cost invariant (cached <= input) holds.
        assert rec.cached_tokens == 100

    def test_failure_status_and_error_fields(self) -> None:
        rec = build_call_record(
            _env(status="failure", error_code="TimeoutError", error_message="boom"),
            {},
        )
        assert rec.status == "failure"
        assert rec.error_code == "TimeoutError"
        assert rec.error_message == "boom"

    def test_missing_headers_default_to_unknown(self) -> None:
        rec = build_call_record(_env(headers={}), {})
        assert rec.tags.agent_name == "unknown"
        assert rec.tags.tenant_id == "unknown"
        assert rec.tags.session_id == "unknown"


class TestLoadCatalog:
    def test_empty_path_returns_empty_catalog(self) -> None:
        assert _load_catalog("") == {}

    def test_missing_file_returns_empty_catalog(self) -> None:
        assert _load_catalog("/nonexistent/litellm-config.yaml") == {}

    def test_loads_model_list_from_canonical_config(self) -> None:
        config = (
            Path(__file__).resolve().parents[4]
            / "infra"
            / "c8_gateway"
            / "config"
            / "litellm-config.yaml"
        )
        catalog = _load_catalog(str(config))
        assert "claude-haiku-fast" in catalog
        assert catalog["claude-haiku-fast"].cost_per_million_input > 0
