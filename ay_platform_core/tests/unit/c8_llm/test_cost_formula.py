# =============================================================================
# File: test_cost_formula.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_cost_formula.py
# Description: Unit tests for the normative cost formula (800-SPEC Appendix
#              8.2). Every number in these tests is derived from the
#              formula as written — drift from the formula is a spec
#              violation, not a test bug.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c8_llm.catalog import Feature
from ay_platform_core.c8_llm.config import ModelInfo
from ay_platform_core.c8_llm.cost import compute_cost


def _model_info(
    *,
    cost_in: float = 3.0,
    cost_out: float = 15.0,
    cost_cached: float | None = None,
) -> ModelInfo:
    return ModelInfo(
        display_name="Test Model",
        features=[Feature.CHAT_COMPLETION],
        context_window=100_000,
        cost_per_million_input=cost_in,
        cost_per_million_output=cost_out,
        cost_per_million_cached=cost_cached,
    )


@pytest.mark.unit
class TestCostFormula:
    def test_simple_case_no_caching(self) -> None:
        # 1_000_000 input at $3/M, 500_000 output at $15/M
        breakdown = compute_cost(
            input_tokens=1_000_000,
            output_tokens=500_000,
            cached_tokens=0,
            model_info=_model_info(),
        )
        assert breakdown.input_cost_usd == pytest.approx(3.00)
        assert breakdown.cached_cost_usd == pytest.approx(0.0)
        assert breakdown.output_cost_usd == pytest.approx(7.50)
        assert breakdown.total_usd == pytest.approx(10.50)

    def test_cached_default_discount_is_ten_percent(self) -> None:
        # 1_000_000 input of which 900_000 cached; default cached rate = 10%
        breakdown = compute_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cached_tokens=900_000,
            model_info=_model_info(cost_in=10.0),
        )
        # Non-cached: 100_000 tokens x $10/M = $1.00
        assert breakdown.input_cost_usd == pytest.approx(1.00)
        # Cached: 900_000 tokens x $1.00/M = $0.90
        assert breakdown.cached_cost_usd == pytest.approx(0.90)

    def test_explicit_cached_rate_overrides_default(self) -> None:
        breakdown = compute_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cached_tokens=1_000_000,
            model_info=_model_info(cost_in=10.0, cost_cached=0.5),
        )
        # All tokens are cached at $0.50/M
        assert breakdown.cached_cost_usd == pytest.approx(0.50)
        assert breakdown.input_cost_usd == pytest.approx(0.0)

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            compute_cost(
                input_tokens=-1,
                output_tokens=0,
                cached_tokens=0,
                model_info=_model_info(),
            )

    def test_cached_exceeding_input_rejected(self) -> None:
        with pytest.raises(ValueError, match="SHALL NOT exceed"):
            compute_cost(
                input_tokens=100,
                output_tokens=0,
                cached_tokens=200,
                model_info=_model_info(),
            )

    def test_zero_tokens_yields_zero_cost(self) -> None:
        breakdown = compute_cost(
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            model_info=_model_info(),
        )
        assert breakdown.total_usd == 0.0
