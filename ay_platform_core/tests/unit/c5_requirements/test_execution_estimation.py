# =============================================================================
# File: test_execution_estimation.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_execution_estimation.py
# Description: Deterministic effort classification — R-310-171, Q-310-008.
#
#              THE TEST THAT MATTERS MOST is the reproducibility one. The
#              whole argument for answering Q-310-008 deterministically is
#              that R-310-174 cannot enforce an overrun against an estimate
#              that moves between runs. If that property were ever lost,
#              the overrun guard would silently stop guarding — so it is
#              pinned explicitly rather than assumed from the absence of
#              randomness.
#
# @relation validates:R-310-171
# @relation validates:R-310-172
# =============================================================================

from __future__ import annotations

from ay_platform_core.c5_requirements.execution.estimation import (
    DEEP_CASCADE_THRESHOLD,
    WIDE_IMPACT_THRESHOLD,
    UnitFeatures,
    classify,
    estimate,
    explain,
    group_by_effort,
    proposed_mode,
)
from ay_platform_core.c5_requirements.execution.models import (
    EffortClass,
    ExecutionMode,
)


def _unit(unit_id: str = "CT-001", **overrides: object) -> UnitFeatures:
    base: dict[str, object] = {
        "unit_id": unit_id,
        "impact_nodes": 2,
        "layers_crossed": 1,
        "containers": 1,
    }
    base.update(overrides)
    return UnitFeatures(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reproducibility — the property R-310-174 depends on
# ---------------------------------------------------------------------------


def test_the_same_features_always_yield_the_same_estimate() -> None:
    """Without this, R-310-174's overrun guard has nothing stable to compare.

    An estimate re-rolled per run cannot be exceeded in any meaningful
    sense: the guard would measure against a number that already moved.
    """
    units = (_unit("CT-001"), _unit("CT-002", impact_nodes=7))
    first = estimate(units, ExecutionMode.END_TO_END)
    for _ in range(5):
        assert estimate(units, ExecutionMode.END_TO_END) == first


def test_the_same_features_always_yield_the_same_class() -> None:
    features = _unit(impact_nodes=20)
    assert {classify(features) for _ in range(5)} == {classify(features)}


def test_feature_order_does_not_change_the_total() -> None:
    a, b = _unit("CT-001"), _unit("CT-002", impact_nodes=9)
    assert estimate((a, b), ExecutionMode.END_TO_END) == estimate(
        (b, a), ExecutionMode.END_TO_END
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_a_narrow_single_container_unit_is_batchable() -> None:
    assert classify(_unit()) is EffortClass.BATCHABLE


def test_a_wide_impact_set_requires_arbitration() -> None:
    assert (
        classify(_unit(impact_nodes=WIDE_IMPACT_THRESHOLD + 1))
        is EffortClass.ARBITRATION_REQUIRED
    )


def test_exactly_at_the_impact_threshold_is_still_batchable() -> None:
    """The boundary is pinned so a later tweak cannot drift it silently."""
    assert (
        classify(_unit(impact_nodes=WIDE_IMPACT_THRESHOLD)) is EffortClass.BATCHABLE
    )


def test_touching_more_than_one_container_requires_arbitration() -> None:
    assert classify(_unit(containers=2)) is EffortClass.ARBITRATION_REQUIRED


def test_a_deep_cascade_requires_reflection() -> None:
    assert (
        classify(_unit(layers_crossed=DEEP_CASCADE_THRESHOLD))
        is EffortClass.REFLECTION_REQUIRED
    )


def test_one_layer_below_the_cascade_threshold_is_not_reflection() -> None:
    assert (
        classify(_unit(layers_crossed=DEEP_CASCADE_THRESHOLD - 1))
        is not EffortClass.REFLECTION_REQUIRED
    )


def test_a_requested_architecture_decision_outranks_every_measurement() -> None:
    """A human has already said this needs thought; no metric overrides that."""
    trivial = _unit(
        impact_nodes=0,
        layers_crossed=0,
        containers=1,
        architecture_decision_requested=True,
    )
    assert classify(trivial) is EffortClass.REFLECTION_REQUIRED


def test_reflection_outranks_arbitration_when_both_would_apply() -> None:
    both = _unit(impact_nodes=99, layers_crossed=DEEP_CASCADE_THRESHOLD)
    assert classify(both) is EffortClass.REFLECTION_REQUIRED


# ---------------------------------------------------------------------------
# R-310-172 — the mode follows from the class
# ---------------------------------------------------------------------------


def test_only_batchable_work_runs_through() -> None:
    assert proposed_mode(EffortClass.BATCHABLE) is ExecutionMode.END_TO_END


def test_arbitration_and_reflection_both_gate() -> None:
    for effort in (
        EffortClass.ARBITRATION_REQUIRED,
        EffortClass.REFLECTION_REQUIRED,
    ):
        assert proposed_mode(effort) is ExecutionMode.STEP_BY_STEP


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


def test_tokens_grow_with_the_impact_set_a_unit_drags_along() -> None:
    narrow = estimate((_unit(impact_nodes=1),), ExecutionMode.END_TO_END)
    wide = estimate((_unit(impact_nodes=30),), ExecutionMode.END_TO_END)
    assert wide.tokens > narrow.tokens


def test_review_items_count_the_nodes_a_human_must_disposition() -> None:
    """One per impacted node, plus the unit itself (R-310-150)."""
    single = estimate((_unit(impact_nodes=5),), ExecutionMode.END_TO_END)
    assert single.review_items == 6


def test_cost_follows_tokens() -> None:
    result = estimate((_unit(impact_nodes=10),), ExecutionMode.END_TO_END)
    assert result.cost_eur > 0
    bigger = estimate((_unit(impact_nodes=100),), ExecutionMode.END_TO_END)
    assert bigger.cost_eur > result.cost_eur


def test_a_gated_step_is_estimated_at_zero_duration() -> None:
    """E-310-004: reviewer availability sets it, and that is unobservable here."""
    gated = estimate((_unit(),) * 1, ExecutionMode.STEP_BY_STEP)
    assert gated.duration_min == 0
    assert gated.tokens > 0


def test_an_end_to_end_step_is_estimated_a_duration() -> None:
    units = tuple(_unit(f"CT-{i:03d}") for i in range(100))
    assert estimate(units, ExecutionMode.END_TO_END).duration_min > 0


def test_an_empty_step_estimates_to_nothing() -> None:
    result = estimate((), ExecutionMode.END_TO_END)
    assert result.tokens == 0
    assert result.review_items == 0
    assert result.cost_eur == 0.0


def test_an_estimate_over_a_realistic_batch_stays_representable() -> None:
    """R-310-300 assumes 10 000 to 30 000 supplied requirements."""
    units = tuple(_unit(f"CT-{i:05d}", impact_nodes=4) for i in range(30_000))
    result = estimate(units, ExecutionMode.END_TO_END)
    assert result.tokens > 0
    assert result.review_items == 30_000 * 5


# ---------------------------------------------------------------------------
# Explanation — a reviewer must be able to check the classification
# ---------------------------------------------------------------------------


def test_the_explanation_names_the_rule_that_fired() -> None:
    wide = _unit("CT-007", impact_nodes=WIDE_IMPACT_THRESHOLD + 5)
    said = explain(wide)
    assert "CT-007" in said
    assert "arbitration-required" in said
    assert str(WIDE_IMPACT_THRESHOLD) in said


def test_the_explanation_cites_the_requirement_for_a_requested_decision() -> None:
    assert "R-310-148" in explain(_unit(architecture_decision_requested=True))


def test_the_explanation_names_the_layer_count_for_a_deep_cascade() -> None:
    said = explain(_unit(layers_crossed=DEEP_CASCADE_THRESHOLD + 1))
    assert "container layers" in said
    assert str(DEEP_CASCADE_THRESHOLD + 1) in said


def test_the_explanation_names_the_container_count() -> None:
    assert "touches 3 containers" in explain(_unit(containers=3))


def test_a_batchable_unit_is_told_it_is_below_every_threshold() -> None:
    said = explain(_unit())
    assert "batchable" in said
    assert "below every threshold" in said


# ---------------------------------------------------------------------------
# Grouping — what makes a 30 000-unit batch ratifiable
# ---------------------------------------------------------------------------


def test_units_are_grouped_into_one_step_per_effort_class() -> None:
    units = (
        _unit("CT-001"),
        _unit("CT-002"),
        _unit("CT-003", containers=2),
        _unit("CT-004", layers_crossed=DEEP_CASCADE_THRESHOLD),
    )
    grouped = group_by_effort(units)
    assert len(grouped[EffortClass.BATCHABLE]) == 2
    assert len(grouped[EffortClass.ARBITRATION_REQUIRED]) == 1
    assert len(grouped[EffortClass.REFLECTION_REQUIRED]) == 1


def test_grouping_reads_from_cheap_to_expensive() -> None:
    units = (
        _unit("CT-004", layers_crossed=DEEP_CASCADE_THRESHOLD),
        _unit("CT-001"),
        _unit("CT-003", containers=2),
    )
    assert list(group_by_effort(units)) == [
        EffortClass.BATCHABLE,
        EffortClass.ARBITRATION_REQUIRED,
        EffortClass.REFLECTION_REQUIRED,
    ]


def test_an_absent_class_is_not_an_empty_group() -> None:
    """An empty step cannot be constructed, so it must not be proposed."""
    grouped = group_by_effort((_unit(),))
    assert list(grouped) == [EffortClass.BATCHABLE]


def test_grouping_nothing_yields_nothing() -> None:
    assert group_by_effort(()) == {}


def test_every_unit_lands_in_exactly_one_group() -> None:
    units = tuple(_unit(f"CT-{i:03d}", impact_nodes=i) for i in range(40))
    grouped = group_by_effort(units)
    placed = [unit.unit_id for group in grouped.values() for unit in group]
    assert sorted(placed) == sorted(unit.unit_id for unit in units)
    assert len(placed) == len(set(placed))
