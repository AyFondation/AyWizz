# =============================================================================
# File: cost.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/cost.py
# Description: Normative cost-computation formula from Appendix 8.2 of
#              800-SPEC-LLM-ABSTRACTION. Pure function, deterministic,
#              exposed as a reusable utility for:
#                - the LiteLLM cost-tracker callback (populates llm_calls.cost_usd)
#                - budget evaluation (R-800-062)
#                - admin aggregation endpoints (R-800-073)
#              Ten percent cached discount default matches R-800-071.
#
# @relation implements:R-800-071
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass

from ay_platform_core.c8_llm.config import ModelInfo

# Default ratio used when a provider doesn't declare `cost_per_million_cached`
# explicitly — Anthropic and OpenAI both charge ~10% of the standard rate as
# of the 800-SPEC baseline.
_DEFAULT_CACHED_DISCOUNT = 0.10


@dataclass(frozen=True, slots=True)
class CostBreakdownTokens:
    """Detailed breakdown of a computed cost — useful for audit + UI."""

    input_cost_usd: float
    cached_cost_usd: float
    output_cost_usd: float

    @property
    def total_usd(self) -> float:
        return self.input_cost_usd + self.cached_cost_usd + self.output_cost_usd


def compute_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    model_info: ModelInfo,
) -> CostBreakdownTokens:
    """Appendix 8.2 formula.

    `cached_tokens` SHALL be a subset of `input_tokens`. The billable
    portion of input is `(input_tokens - cached_tokens)` at the standard
    input rate; cached tokens are charged separately at the cached rate.
    Output tokens are charged at the standard output rate.

    Raises ValueError if `cached_tokens > input_tokens` (spec invariant).
    """
    if input_tokens < 0 or output_tokens < 0 or cached_tokens < 0:
        raise ValueError("token counts SHALL be non-negative")
    if cached_tokens > input_tokens:
        raise ValueError(
            f"cached_tokens ({cached_tokens}) SHALL NOT exceed "
            f"input_tokens ({input_tokens}) — the former is a subset of the latter"
        )

    cached_rate = (
        model_info.cost_per_million_cached
        if model_info.cost_per_million_cached is not None
        else model_info.cost_per_million_input * _DEFAULT_CACHED_DISCOUNT
    )

    non_cached_input = input_tokens - cached_tokens
    input_cost = (non_cached_input * model_info.cost_per_million_input) / 1_000_000
    cached_cost = (cached_tokens * cached_rate) / 1_000_000
    output_cost = (output_tokens * model_info.cost_per_million_output) / 1_000_000
    return CostBreakdownTokens(
        input_cost_usd=input_cost,
        cached_cost_usd=cached_cost,
        output_cost_usd=output_cost,
    )
