# =============================================================================
# File: catalog.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/catalog.py
# Description: Normative feature catalog (R-800-040) and agent-to-feature
#              catalog (§4.6 of 800-SPEC-LLM-ABSTRACTION). These tables are
#              the canonical source of truth — they mirror the spec and are
#              consulted at config-validation time to reject misconfigured
#              agent→model mappings.
#
# @relation implements:R-800-040
# @relation implements:R-800-050
# @relation implements:R-800-051
# =============================================================================

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Feature(StrEnum):
    """Capabilities declared in the feature catalog (R-800-040)."""

    CHAT_COMPLETION = "chat_completion"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUTS = "structured_outputs"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    EXTENDED_THINKING = "extended_thinking"
    PROMPT_CACHING = "prompt_caching"
    STREAMING = "streaming"


class ModelClass(StrEnum):
    """Tiers referenced in the per-agent requirements table."""

    FLAGSHIP = "flagship"
    MID_TIER = "mid_tier"
    FAST_TIER = "fast_tier"


# The full feature catalog — mirrors R-800-040's enumeration.
FEATURE_CATALOG: Final[frozenset[Feature]] = frozenset(Feature)


# Per-agent LLM requirements — mirrors §4.6 of 800-SPEC.
#
# Each row is: (agent_name, required_features, preferred_class).
# `required_features` is the MINIMUM set a routed model SHALL support.
# `preferred_class` is advisory for catalog authors selecting models.
AGENT_CATALOG: Final[dict[str, tuple[frozenset[Feature], ModelClass]]] = {
    "architect": (
        frozenset({
            Feature.CHAT_COMPLETION,
            Feature.STREAMING,
            Feature.LONG_CONTEXT,
            Feature.PROMPT_CACHING,
            Feature.EXTENDED_THINKING,
        }),
        ModelClass.FLAGSHIP,
    ),
    "planner": (
        frozenset({
            Feature.CHAT_COMPLETION,
            Feature.STREAMING,
            Feature.STRUCTURED_OUTPUTS,
            Feature.LONG_CONTEXT,
        }),
        ModelClass.MID_TIER,
    ),
    "implementer": (
        frozenset({
            Feature.CHAT_COMPLETION,
            Feature.STREAMING,
            Feature.TOOL_CALLING,
            Feature.LONG_CONTEXT,
        }),
        ModelClass.MID_TIER,
    ),
    "spec-reviewer": (
        frozenset({
            Feature.CHAT_COMPLETION,
            Feature.STRUCTURED_OUTPUTS,
            Feature.LONG_CONTEXT,
        }),
        ModelClass.MID_TIER,
    ),
    "quality-reviewer": (
        frozenset({
            Feature.CHAT_COMPLETION,
            Feature.STRUCTURED_OUTPUTS,
            Feature.LONG_CONTEXT,
        }),
        ModelClass.MID_TIER,
    ),
    "sub-agent": (
        frozenset({Feature.CHAT_COMPLETION, Feature.TOOL_CALLING}),
        ModelClass.FAST_TIER,
    ),
}


def required_features(agent_name: str) -> frozenset[Feature]:
    """Return the minimum feature set for a registered agent.

    Raises KeyError if the agent is not registered in the catalog;
    per R-800-051 the caller SHALL fall back to the default model and log
    a warning when this happens.
    """
    return AGENT_CATALOG[agent_name][0]


def preferred_class(agent_name: str) -> ModelClass:
    return AGENT_CATALOG[agent_name][1]
