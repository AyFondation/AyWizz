# =============================================================================
# File: test_execution_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_execution_models.py
# Description: The negotiated treatment plan — R-310-170 … R-310-176.
#
# @relation validates:R-310-170
# @relation validates:R-310-171
# @relation validates:R-310-172
# @relation validates:R-310-173
# @relation validates:R-310-175
# @relation validates:R-310-176
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.execution.models import (
    MIN_RATIONALE,
    Amendment,
    Batch,
    BatchKind,
    EffortClass,
    ExecutionMode,
    PlanStep,
    Ratification,
    StepEstimate,
    StepState,
    TreatmentPlan,
    TreatmentReport,
)

_NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
_BATCH = Batch(kind=BatchKind.CHANGE_SET, source="CUST-2026-W14", count=340)


def _estimate(**overrides: object) -> StepEstimate:
    base: dict[str, object] = {
        "tokens": 4_200_000,
        "cost_eur": 31.4,
        "duration_min": 48,
        "review_items": 612,
    }
    base.update(overrides)
    return StepEstimate.model_validate(base)


def _step(**overrides: object) -> PlanStep:
    base: dict[str, object] = {
        "step_id": "st1",
        "scope": ("CT-001", "CT-002"),
        "effort": EffortClass.BATCHABLE,
        "mode": ExecutionMode.END_TO_END,
        "estimate": _estimate(),
    }
    base.update(overrides)
    return PlanStep.model_validate(base)


def _plan(**overrides: object) -> TreatmentPlan:
    base: dict[str, object] = {
        "plan_id": "TP-0114",
        "project_id": "adas",
        "batch": _BATCH,
        "steps": (_step(),),
        "proposed_by": "agent:planner",
        "proposed_at": _NOW,
    }
    base.update(overrides)
    return TreatmentPlan.model_validate(base)


# ---------------------------------------------------------------------------
# R-310-171 — a step cannot exist without its estimate
# ---------------------------------------------------------------------------


def test_a_step_without_an_estimate_cannot_exist() -> None:
    with pytest.raises(ValidationError):
        PlanStep.model_validate(
            {
                "step_id": "st1",
                "scope": ("CT-001",),
                "effort": "batchable",
                "mode": "end-to-end",
            }
        )


def test_review_items_has_no_default_because_reviewer_capacity_binds_first() -> None:
    """A default of zero would silently claim the step generates no review work."""
    with pytest.raises(ValidationError):
        StepEstimate.model_validate(
            {"tokens": 1000, "cost_eur": 1.0, "duration_min": 5}
        )


def test_an_estimate_carries_all_four_figures() -> None:
    estimate = _estimate()
    assert estimate.tokens == 4_200_000
    assert estimate.cost_eur == 31.4
    assert estimate.duration_min == 48
    assert estimate.review_items == 612


def test_a_negative_figure_is_refused() -> None:
    with pytest.raises(ValidationError):
        _estimate(tokens=-1)


def test_a_plan_sums_its_steps_into_a_total() -> None:
    plan = _plan(
        steps=(
            _step(estimate=_estimate(tokens=100, cost_eur=1.5, review_items=10)),
            _step(
                step_id="st2",
                scope=("CT-003",),
                estimate=_estimate(tokens=200, cost_eur=2.25, review_items=20),
            ),
        )
    )
    total = plan.total_estimate
    assert total.tokens == 300
    assert total.cost_eur == 3.75
    assert total.review_items == 30


def test_a_plan_with_no_steps_cannot_exist() -> None:
    with pytest.raises(ValidationError, match="R-310-171"):
        _plan(steps=())


# ---------------------------------------------------------------------------
# Step scope integrity
# ---------------------------------------------------------------------------


def test_an_empty_step_is_refused() -> None:
    with pytest.raises(ValidationError, match="treats nothing"):
        _step(scope=())


def test_a_step_listing_one_unit_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="lists an identifier twice"):
        _step(scope=("CT-001", "CT-001"))


def test_two_steps_treating_the_same_unit_are_refused() -> None:
    """Double-counting a unit double-counts its cost AND its review items."""
    with pytest.raises(ValidationError, match="double-counts"):
        _plan(
            steps=(
                _step(scope=("CT-001", "CT-002")),
                _step(step_id="st2", scope=("CT-002",)),
            )
        )


def test_duplicate_step_identifiers_are_refused() -> None:
    with pytest.raises(ValidationError, match="unique within a plan"):
        _plan(steps=(_step(), _step(scope=("CT-009",))))


def test_a_plan_counts_the_units_it_treats() -> None:
    plan = _plan(
        steps=(_step(scope=("CT-001", "CT-002")), _step(step_id="st2", scope=("CT-003",)))
    )
    assert plan.unit_count == 3


# ---------------------------------------------------------------------------
# R-310-172 — the mode is a property of the plan
# ---------------------------------------------------------------------------


def test_a_gated_step_must_not_claim_a_duration() -> None:
    """E-310-004: a gated step's duration is set by reviewer availability."""
    with pytest.raises(ValidationError, match="reviewer availability"):
        _step(mode=ExecutionMode.STEP_BY_STEP, estimate=_estimate(duration_min=30))


def test_a_gated_step_with_zero_duration_is_accepted() -> None:
    step = _step(
        mode=ExecutionMode.STEP_BY_STEP, estimate=_estimate(duration_min=0)
    )
    assert step.mode is ExecutionMode.STEP_BY_STEP


def test_a_plan_names_its_gated_steps() -> None:
    plan = _plan(
        steps=(
            _step(),
            _step(
                step_id="st2",
                scope=("CT-003",),
                effort=EffortClass.REFLECTION_REQUIRED,
                mode=ExecutionMode.STEP_BY_STEP,
                estimate=_estimate(duration_min=0),
            ),
        )
    )
    assert plan.gated_step_ids == ("st2",)


def test_the_three_effort_classes_are_the_whole_set() -> None:
    assert {e.value for e in EffortClass} == {
        "batchable",
        "arbitration-required",
        "reflection-required",
    }


# ---------------------------------------------------------------------------
# R-310-175 — ratification is a record tied to a plan VERSION
# ---------------------------------------------------------------------------


def test_an_unratified_plan_says_so() -> None:
    assert _plan().is_ratified is False


def test_a_ratified_plan_says_so() -> None:
    plan = _plan(
        ratification=Ratification(plan_version=1, actor="o.mathieu", at=_NOW)
    )
    assert plan.is_ratified is True


def test_an_amendment_does_not_inherit_its_predecessors_ratification() -> None:
    """R-310-173 lets a plan be amended; R-310-170 says work needs approval.

    An amended plan that kept its old approval would let execution proceed
    on terms nobody agreed to.
    """
    plan = _plan(
        version=2,
        ratification=Ratification(plan_version=1, actor="o.mathieu", at=_NOW),
        amendments=(
            Amendment(
                from_version=1,
                to_version=2,
                rationale="The batchable estimate was wrong by an order of magnitude.",
                actor="o.mathieu",
                at=_NOW,
                replaced_step_id="st0",
                into_step_ids=("st1", "st2"),
            ),
        ),
    )
    assert plan.is_ratified is False


def test_a_ratification_cannot_approve_a_version_that_does_not_exist() -> None:
    with pytest.raises(ValidationError, match="does not exist yet"):
        _plan(ratification=Ratification(plan_version=4, actor="o.mathieu", at=_NOW))


def test_a_ratification_records_actor_and_instant() -> None:
    ratification = Ratification(plan_version=1, actor="o.mathieu", at=_NOW)
    assert ratification.actor == "o.mathieu"
    assert ratification.at == _NOW


# ---------------------------------------------------------------------------
# R-310-173 — amendment
# ---------------------------------------------------------------------------


def test_an_amendment_must_advance_the_version() -> None:
    with pytest.raises(ValidationError):
        Amendment(
            from_version=2,
            to_version=2,
            rationale="Re-splitting the oversized step.",
            actor="o.mathieu",
            at=_NOW,
            replaced_step_id="st1",
            into_step_ids=("st1a", "st1b"),
        )


def test_an_amendment_must_actually_split() -> None:
    """One step into one step is an edit, not a re-split."""
    with pytest.raises(ValidationError, match="at least two steps"):
        Amendment(
            from_version=1,
            to_version=2,
            rationale="Re-splitting the oversized step.",
            actor="o.mathieu",
            at=_NOW,
            replaced_step_id="st1",
            into_step_ids=("st1a",),
        )


def test_an_amendment_needs_a_rationale() -> None:
    with pytest.raises(ValidationError):
        Amendment(
            from_version=1,
            to_version=2,
            rationale="oops",
            actor="o.mathieu",
            at=_NOW,
            replaced_step_id="st1",
            into_step_ids=("st1a", "st1b"),
        )


def test_the_amendment_trail_must_account_for_the_claimed_version() -> None:
    with pytest.raises(ValidationError, match="amendment trail"):
        _plan(
            version=3,
            amendments=(
                Amendment(
                    from_version=1,
                    to_version=2,
                    rationale="Re-splitting the oversized step.",
                    actor="o.mathieu",
                    at=_NOW,
                    replaced_step_id="st0",
                    into_step_ids=("st1", "st2"),
                ),
            ),
        )


def test_a_plan_names_its_completed_steps_so_an_amendment_can_spare_them() -> None:
    plan = _plan(
        steps=(
            _step(state=StepState.COMPLETED),
            _step(step_id="st2", scope=("CT-003",)),
        )
    )
    assert plan.completed_step_ids == ("st1",)


# ---------------------------------------------------------------------------
# R-310-174 — suspension is recorded with its reason
# ---------------------------------------------------------------------------


def test_a_suspended_step_must_say_why() -> None:
    with pytest.raises(ValidationError, match="records why it suspended"):
        _step(state=StepState.SUSPENDED)


def test_a_running_step_must_not_carry_a_suspension_reason() -> None:
    with pytest.raises(ValidationError, match="only a suspended step"):
        _step(state=StepState.RUNNING, suspended_reason="over budget")


def test_a_suspended_step_is_named_by_the_plan() -> None:
    plan = _plan(
        steps=(
            _step(
                state=StepState.SUSPENDED,
                suspended_reason="token estimate exceeded by 40 %",
            ),
        )
    )
    assert plan.suspended_step_ids == ("st1",)


def test_suspension_is_not_failure() -> None:
    """A step that went over did not go wrong; the remedy differs."""
    suspended = _step(
        state=StepState.SUSPENDED, suspended_reason="token estimate exceeded"
    )
    assert suspended.is_terminal is False
    assert _step(state=StepState.FAILED).is_terminal is True


def test_a_plan_is_complete_only_when_every_step_is_terminal() -> None:
    assert _plan(steps=(_step(state=StepState.COMPLETED),)).is_complete is True
    assert _plan(steps=(_step(state=StepState.PENDING),)).is_complete is False
    assert (
        _plan(
            steps=(
                _step(
                    state=StepState.SUSPENDED, suspended_reason="over the token estimate"
                ),
            )
        ).is_complete
        is False
    )


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def test_asking_for_an_unknown_step_is_an_error_not_a_default() -> None:
    with pytest.raises(KeyError, match="st9"):
        _plan().step("st9")


def test_a_plan_round_trips_through_its_serialised_form() -> None:
    plan = _plan(
        ratification=Ratification(plan_version=1, actor="o.mathieu", at=_NOW)
    )
    assert TreatmentPlan.model_validate(plan.model_dump()) == plan


def test_the_minimum_rationale_length_refuses_a_token_answer() -> None:
    assert MIN_RATIONALE >= 12


# ---------------------------------------------------------------------------
# R-310-176 — the treatment report
# ---------------------------------------------------------------------------


def test_a_report_computes_its_coverage_delta() -> None:
    report = TreatmentReport(
        plan_id="TP-0114",
        project_id="adas",
        plan_version=1,
        generated_at=_NOW,
        coverage_before=120,
        coverage_after=287,
    )
    assert report.coverage_delta == 167


def test_a_report_separates_accepted_from_auto_accepted() -> None:
    """R-310-007: every coverage figure must be able to tell the two apart."""
    report = TreatmentReport(
        plan_id="TP-0114",
        project_id="adas",
        plan_version=1,
        generated_at=_NOW,
        review_outcomes={"accepted": 180, "auto-accepted": 95, "rejected": 12},
    )
    assert report.review_outcomes["accepted"] == 180
    assert report.review_outcomes["auto-accepted"] == 95
    assert sum(report.review_outcomes.values()) == 287


def test_a_report_with_gaps_is_not_clean() -> None:
    report = TreatmentReport(
        plan_id="TP-0114",
        project_id="adas",
        plan_version=1,
        generated_at=_NOW,
        remaining_gaps=("CUST-041",),
    )
    assert report.is_clean is False


def test_a_report_with_returns_awaiting_arbitration_is_not_clean() -> None:
    report = TreatmentReport(
        plan_id="TP-0114",
        project_id="adas",
        plan_version=1,
        generated_at=_NOW,
        returns_awaiting_arbitration=("CUST-077",),
    )
    assert report.is_clean is False


def test_a_report_with_nothing_outstanding_is_clean() -> None:
    report = TreatmentReport(
        plan_id="TP-0114", project_id="adas", plan_version=1, generated_at=_NOW
    )
    assert report.is_clean is True


def test_a_negative_coverage_delta_is_representable() -> None:
    """A change can leave fewer requirements answered than before.

    Clamping it to zero would hide a regression, which is the one figure a
    treatment report exists to surface.
    """
    report = TreatmentReport(
        plan_id="TP-0114",
        project_id="adas",
        plan_version=1,
        generated_at=_NOW,
        coverage_before=200,
        coverage_after=180,
    )
    assert report.coverage_delta == -20
