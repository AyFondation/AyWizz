# =============================================================================
# File: test_execution_budget.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_execution_budget.py
# Description: The overrun guard — R-310-174.
#
# @relation validates:R-310-174
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c5_requirements.execution.budget import (
    DEFAULT_MARGIN,
    BudgetFigure,
    Consumption,
    allowance,
    check,
)
from ay_platform_core.c5_requirements.execution.models import StepEstimate

_ESTIMATE = StepEstimate(
    tokens=1_000_000, cost_eur=7.5, duration_min=20, review_items=100
)


# ---------------------------------------------------------------------------
# The allowance
# ---------------------------------------------------------------------------


def test_the_allowance_is_the_estimate_plus_the_margin() -> None:
    assert allowance(1000, 0.25) == 1250


def test_a_zero_margin_allows_exactly_the_estimate() -> None:
    assert allowance(1000, 0.0) == 1000


def test_a_negative_margin_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        allowance(1000, -0.1)


def test_the_margin_is_fractional_so_it_scales() -> None:
    """An absolute margin suiting a 300-token step is noise on 4 million."""
    assert allowance(300) < allowance(4_000_000)
    assert allowance(4_000_000) == int(4_000_000 * (1 + DEFAULT_MARGIN))


# ---------------------------------------------------------------------------
# Continuing
# ---------------------------------------------------------------------------


def test_a_step_within_every_allowance_may_continue() -> None:
    verdict = check(
        _ESTIMATE, Consumption(tokens=900_000, objects=90, review_items=90)
    )
    assert verdict.may_continue is True
    assert "within every allowance" in verdict.explain()


def test_a_step_exactly_at_its_allowance_may_continue() -> None:
    """The ceiling is inclusive, pinned so a later tweak cannot drift it."""
    verdict = check(
        _ESTIMATE,
        Consumption(tokens=allowance(1_000_000), objects=0, review_items=0),
    )
    assert verdict.may_continue is True


def test_a_step_that_consumed_nothing_may_continue() -> None:
    assert check(_ESTIMATE, Consumption()).may_continue is True


# ---------------------------------------------------------------------------
# Breaching — the report, not a boolean
# ---------------------------------------------------------------------------


def test_a_token_overrun_suspends_and_names_the_figure() -> None:
    verdict = check(_ESTIMATE, Consumption(tokens=2_000_000))
    assert verdict.may_continue is False
    assert [breach.figure for breach in verdict.breaches] == [BudgetFigure.TOKENS]


def test_the_breach_carries_estimate_allowance_and_actual() -> None:
    """Without all three, 'raise the estimate or re-split' is a guess."""
    verdict = check(_ESTIMATE, Consumption(tokens=2_000_000))
    breach = verdict.breaches[0]
    assert breach.estimated == 1_000_000
    assert breach.allowed == 1_250_000
    assert breach.actual == 2_000_000
    assert "estimated 1,000,000" in breach.explain()
    assert "consumed 2,000,000" in breach.explain()


def test_the_breach_reports_the_overrun_fraction() -> None:
    verdict = check(_ESTIMATE, Consumption(tokens=1_500_000))
    assert verdict.breaches[0].overrun_fraction == 0.5


def test_a_review_item_overrun_suspends() -> None:
    """Reviewer capacity binds before budget does (R-310-171)."""
    verdict = check(_ESTIMATE, Consumption(review_items=500))
    assert BudgetFigure.REVIEW_ITEMS in {b.figure for b in verdict.breaches}


def test_an_object_count_overrun_suspends() -> None:
    verdict = check(_ESTIMATE, Consumption(objects=900), object_estimate=100)
    assert BudgetFigure.OBJECTS in {b.figure for b in verdict.breaches}


def test_every_breached_figure_is_reported_at_once() -> None:
    """A step over on tokens and review items must not suspend twice."""
    verdict = check(
        _ESTIMATE,
        Consumption(tokens=9_000_000, objects=9_000, review_items=9_000),
    )
    assert {breach.figure for breach in verdict.breaches} == {
        BudgetFigure.TOKENS,
        BudgetFigure.OBJECTS,
        BudgetFigure.REVIEW_ITEMS,
    }
    explained = verdict.explain()
    for figure in BudgetFigure:
        assert figure.value in explained


def test_duration_is_not_guarded() -> None:
    """R-310-174 names tokens, objects and review items — not duration.

    Suspending work that is proceeding correctly because it is slower than
    guessed would be a worse outcome than finishing late.
    """
    assert "duration" not in {figure.value for figure in BudgetFigure}
    slow = check(
        StepEstimate(tokens=10, cost_eur=0.1, duration_min=1, review_items=10),
        Consumption(tokens=5, objects=5, review_items=5),
    )
    assert slow.may_continue is True


# ---------------------------------------------------------------------------
# Edge cases the guard must not blow up on
# ---------------------------------------------------------------------------


def test_a_zero_estimate_that_consumed_something_breaches() -> None:
    zero = StepEstimate(tokens=0, cost_eur=0.0, duration_min=0, review_items=0)
    verdict = check(zero, Consumption(tokens=1))
    assert verdict.may_continue is False


def test_a_zero_estimate_reports_no_fraction_rather_than_dividing() -> None:
    """A guard whose job is not to blow up must not raise on arithmetic."""
    zero = StepEstimate(tokens=0, cost_eur=0.0, duration_min=0, review_items=0)
    assert check(zero, Consumption(tokens=1)).breaches[0].overrun_fraction == 0.0


def test_the_object_estimate_defaults_to_the_review_item_estimate() -> None:
    """One review item per object touched is the normal case."""
    within = check(_ESTIMATE, Consumption(objects=100))
    assert within.may_continue is True
    beyond = check(_ESTIMATE, Consumption(objects=200))
    assert BudgetFigure.OBJECTS in {b.figure for b in beyond.breaches}


def test_a_tighter_margin_suspends_earlier() -> None:
    consumption = Consumption(tokens=1_100_000)
    assert check(_ESTIMATE, consumption, margin=0.25).may_continue is True
    assert check(_ESTIMATE, consumption, margin=0.05).may_continue is False
