# =============================================================================
# File: test_validator.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_validator.py
# Description: Unit tests — config-time agent/feature cross-check
#              (R-800-050). Exercises success + representative failure
#              modes so regressions in AGENT_CATALOG or the validator are
#              caught before a bad litellm-config.yaml is deployed.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c8_llm.catalog import Feature
from ay_platform_core.c8_llm.config import (
    LiteLLMConfig,
    LiteLLMParams,
    ModelEntry,
    ModelInfo,
)
from ay_platform_core.c8_llm.validator import validate_configuration


def _model(
    name: str, features: list[Feature], *, cost_in: float = 1.0, cost_out: float = 2.0
) -> ModelEntry:
    return ModelEntry(
        model_name=name,
        litellm_params=LiteLLMParams(model=f"anthropic/{name}"),
        model_info=ModelInfo(
            display_name=name,
            features=features,
            context_window=200_000,
            cost_per_million_input=cost_in,
            cost_per_million_output=cost_out,
        ),
    )


_ALL_FEATURES = list(Feature)


@pytest.mark.unit
class TestValidator:
    def test_empty_routes_no_issues(self) -> None:
        cfg = LiteLLMConfig(model_list=[_model("flagship", _ALL_FEATURES)])
        assert validate_configuration(cfg) == []

    def test_route_to_unknown_model_flagged(self) -> None:
        cfg = LiteLLMConfig(
            model_list=[_model("flagship", _ALL_FEATURES)],
            agent_routes={"architect": "does-not-exist"},
        )
        issues = validate_configuration(cfg)
        assert len(issues) == 1
        assert "not declared" in issues[0].message

    def test_agent_missing_required_feature_flagged(self) -> None:
        # architect requires extended_thinking; the model below doesn't have it.
        weak_model = _model(
            "weak",
            [
                Feature.CHAT_COMPLETION,
                Feature.STREAMING,
                Feature.LONG_CONTEXT,
                Feature.PROMPT_CACHING,
                # missing EXTENDED_THINKING
            ],
        )
        cfg = LiteLLMConfig(
            model_list=[weak_model],
            agent_routes={"architect": "weak"},
        )
        issues = validate_configuration(cfg)
        assert len(issues) == 1
        assert "extended_thinking" in issues[0].message
        assert issues[0].agent == "architect"

    def test_valid_full_catalog_no_issues(self) -> None:
        flagship = _model("flagship", _ALL_FEATURES)
        midtier = _model(
            "midtier",
            [
                Feature.CHAT_COMPLETION,
                Feature.STREAMING,
                Feature.LONG_CONTEXT,
                Feature.PROMPT_CACHING,
                Feature.STRUCTURED_OUTPUTS,
                Feature.TOOL_CALLING,
            ],
        )
        fast = _model(
            "fast",
            [Feature.CHAT_COMPLETION, Feature.STREAMING, Feature.TOOL_CALLING],
        )
        cfg = LiteLLMConfig(
            model_list=[flagship, midtier, fast],
            agent_routes={
                "architect": "flagship",
                "planner": "midtier",
                "implementer": "midtier",
                "spec-reviewer": "midtier",
                "quality-reviewer": "midtier",
                "sub-agent": "fast",
                "default": "midtier",
            },
        )
        assert validate_configuration(cfg) == []

    def test_unknown_agent_allowed_no_issues(self) -> None:
        """R-800-051: agent not in catalog is allowed at config time
        (fallback + warning at runtime)."""
        model = _model("midtier", _ALL_FEATURES)
        cfg = LiteLLMConfig(
            model_list=[model],
            agent_routes={"some-future-agent": "midtier"},
        )
        assert validate_configuration(cfg) == []
