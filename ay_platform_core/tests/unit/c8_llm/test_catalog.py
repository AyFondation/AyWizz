# =============================================================================
# File: test_catalog.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_catalog.py
# Description: Unit tests — the feature catalog and agent-to-feature table
#              match 800-SPEC §4.5 / §4.6 exactly. These tables are
#              normative; drift is blocking.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest

from ay_platform_core.c8_llm.catalog import (
    AGENT_CATALOG,
    FEATURE_CATALOG,
    Feature,
    ModelClass,
    preferred_class,
    required_features,
)


@pytest.mark.unit
class TestFeatureCatalog:
    EXPECTED: ClassVar[set[str]] = {
        "chat_completion",
        "tool_calling",
        "structured_outputs",
        "vision",
        "long_context",
        "extended_thinking",
        "prompt_caching",
        "streaming",
    }

    def test_feature_enum_matches_spec(self) -> None:
        assert {f.value for f in Feature} == self.EXPECTED

    def test_feature_catalog_covers_every_feature(self) -> None:
        assert frozenset(Feature) == FEATURE_CATALOG


@pytest.mark.unit
class TestAgentCatalog:
    def test_all_v1_agents_registered(self) -> None:
        expected = {
            "architect",
            "planner",
            "implementer",
            "spec-reviewer",
            "quality-reviewer",
            "sub-agent",
        }
        assert set(AGENT_CATALOG.keys()) == expected

    def test_architect_requires_extended_thinking(self) -> None:
        feats = required_features("architect")
        assert Feature.EXTENDED_THINKING in feats
        assert Feature.PROMPT_CACHING in feats
        assert Feature.LONG_CONTEXT in feats

    def test_sub_agent_minimal_features(self) -> None:
        feats = required_features("sub-agent")
        assert feats == frozenset({Feature.CHAT_COMPLETION, Feature.TOOL_CALLING})

    def test_preferred_class_architect_is_flagship(self) -> None:
        assert preferred_class("architect") == ModelClass.FLAGSHIP

    def test_preferred_class_sub_agent_is_fast_tier(self) -> None:
        assert preferred_class("sub-agent") == ModelClass.FAST_TIER

    def test_unknown_agent_raises(self) -> None:
        with pytest.raises(KeyError):
            required_features("not-an-agent")
