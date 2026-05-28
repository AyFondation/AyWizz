# =============================================================================
# File: test_config_schema.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_config_schema.py
# Description: Unit tests — the Pydantic schema accepts the sample config
#              from `infra/c8_gateway/config/litellm-config.yaml` (ensuring
#              the two stay in sync) and rejects malformed structure.
# =============================================================================

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from pydantic import ValidationError

from ay_platform_core.c8_llm.config import (
    ClientSettings,
    LiteLLMConfig,
    ModelInfo,
)

_CONFIG_DIR = Path(__file__).resolve().parents[4] / "infra" / "c8_gateway" / "config"
_SAMPLE_CONFIG_PATH = _CONFIG_DIR / "litellm-config.yaml"


@pytest.mark.unit
class TestSampleConfigParses:
    def test_sample_file_exists(self) -> None:
        assert _SAMPLE_CONFIG_PATH.is_file(), f"missing sample at {_SAMPLE_CONFIG_PATH}"

    def test_sample_yaml_matches_schema(self) -> None:
        raw = yaml.safe_load(_SAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
        cfg = LiteLLMConfig.model_validate(raw)
        # Spot-checks: every agent in §4.6 is routed, every model declared
        # has a realistic cost.
        assert {"architect", "planner", "implementer", "sub-agent", "default"}.issubset(
            cfg.agent_routes.keys()
        )
        assert all(m.model_info.cost_per_million_input >= 0 for m in cfg.model_list)

    def test_sample_carries_proxy_sections(self) -> None:
        # v3 : the config wires the proxy master key + cost forwarder.
        cfg = LiteLLMConfig.model_validate(
            yaml.safe_load(_SAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
        )
        assert cfg.general_settings is not None and "master_key" in cfg.general_settings
        assert cfg.litellm_settings is not None and "callbacks" in cfg.litellm_settings

    def test_service_agents_routed_by_quality_cost(self) -> None:
        # The C3/C7 service agents are routed explicitly (best quality/cost),
        # not left to fall through to `default`. Claude-only — Ollama is the
        # separate local-dev path, never a proxy route.
        cfg = LiteLLMConfig.model_validate(
            yaml.safe_load(_SAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
        )
        assert {"c3-rag", "c3-docgen", "c7-kg-extractor"}.issubset(cfg.agent_routes.keys())
        model_names = {m.model_name for m in cfg.model_list}
        assert all(m.startswith("claude-") for m in model_names)
        assert all(target in model_names for target in cfg.agent_routes.values())


@pytest.mark.unit
class TestSchemaStrictness:
    _valid_model: ClassVar[dict[str, Any]] = {
        "model_name": "claude-sonnet",
        "litellm_params": {"model": "anthropic/claude-sonnet-4-6"},
        "model_info": {
            "display_name": "Claude Sonnet 4.6",
            "features": ["chat_completion", "streaming"],
            "context_window": 200_000,
            "cost_per_million_input": 3.0,
            "cost_per_million_output": 15.0,
        },
    }

    def test_unknown_root_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LiteLLMConfig.model_validate(
                {"model_list": [self._valid_model], "unknown_root": True}
            )

    def test_unknown_model_info_field_rejected(self) -> None:
        bad = {
            **self._valid_model,
            "model_info": {**self._valid_model["model_info"], "surprise": 1},
        }
        with pytest.raises(ValidationError):
            LiteLLMConfig.model_validate({"model_list": [bad]})

    def test_unknown_feature_rejected(self) -> None:
        bad = {
            **self._valid_model,
            "model_info": {
                **self._valid_model["model_info"],
                "features": ["chat_completion", "telepathy"],
            },
        }
        with pytest.raises(ValidationError):
            LiteLLMConfig.model_validate({"model_list": [bad]})

    def test_negative_cost_rejected(self) -> None:
        bad = {
            **self._valid_model,
            "model_info": {
                **self._valid_model["model_info"],
                "cost_per_million_input": -1.0,
            },
        }
        with pytest.raises(ValidationError):
            LiteLLMConfig.model_validate({"model_list": [bad]})

    def test_model_info_requires_core_fields(self) -> None:
        with pytest.raises(ValidationError):
            ModelInfo.model_validate(
                {"display_name": "x"}  # missing features, context_window, costs
            )


@pytest.mark.unit
class TestClientSettings:
    def test_defaults(self) -> None:
        settings = ClientSettings(_env_file=None)  # type: ignore[call-arg]
        assert settings.gateway_url.endswith("/v1")
        assert settings.request_timeout_seconds > 0
        assert settings.sse_heartbeat_seconds >= 1.0

    def test_prefix_picks_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("C8_GATEWAY_URL", "http://override:9000/v1")
        settings = ClientSettings()
        assert settings.gateway_url == "http://override:9000/v1"
