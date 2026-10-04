# =============================================================================
# File: estimation.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/estimation.py
# Description: Deterministic effort classification and estimation —
#              310-SPEC §4.9 (R-310-171), answering Q-310-008.
#
#              Q-310-008 ASKS whether the effort classification comes from
#              a model judgement or from deterministic features. It is
#              answered here as DETERMINISTIC, and the binding reason is
#              not cost or auditability — it is that R-310-174 requires a
#              step to suspend when it EXCEEDS ITS ESTIMATE. An estimate a
#              model re-rolls differently on every run cannot be overrun
#              in any meaningful sense: the overrun guard would be
#              comparing a measurement against a number that has already
#              moved. A reproducible estimate is what makes the guard a
#              guard.
#
#              The secondary reasons hold too: a deterministic classifier
#              can be pinned by a unit test, and a reviewer asked to
#              ratify a plan can be told WHY a step was called
#              `reflection-required` in terms they can check.
#
#              NO SEAM IS BUILT for a model-based classifier. Adding an
#              injection point for a collaborator nobody has asked for is
#              speculative structure (CLAUDE.md §2); swapping later is a
#              change, not a configuration.
#
#              THE RATES ARE NOT PRETENDING TO BE PRECISE. They are
#              order-of-magnitude figures calibrated to make the overrun
#              margin meaningful, and they are module constants so the one
#              place to recalibrate them is obvious. An estimate's job here
#              is to make "process everything end to end" a priced
#              decision rather than a blank cheque — not to be a forecast.
#
# @relation implements:R-310-171
# @relation implements:R-310-172
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass

from .models import EffortClass, ExecutionMode, StepEstimate

# ---------------------------------------------------------------------------
# Calibration constants — the single place to recalibrate
# ---------------------------------------------------------------------------

#: Tokens spent reading and rewriting one object, including its context.
TOKENS_PER_OBJECT = 12_000

#: Extra tokens per impacted node a unit drags along: the agent must read
#: each one to qualify it (R-310-148).
TOKENS_PER_IMPACT_NODE = 3_000

#: Euro per million tokens, blended across the tiers a run actually uses.
EUR_PER_MILLION_TOKENS = 7.5

#: Wall-clock minutes per unit under end-to-end execution.
MINUTES_PER_UNIT = 0.14

#: Review items a unit generates: one per impacted node to disposition,
#: plus one for the unit itself.
REVIEW_ITEMS_PER_UNIT = 1

#: An impact set wider than this makes a unit arbitration-required: the
#: blast radius is large enough that a human should see it laid out before
#: anything is rewritten.
WIDE_IMPACT_THRESHOLD = 12

#: Crossing this many container layers makes a unit reflection-required.
DEEP_CASCADE_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class UnitFeatures:
    """The measurable facts about one unit of work.

    Every field is observed from the graph or from a recorded decision —
    none is a judgement. That is what makes the classification
    reproducible, and therefore what makes `R-310-174` enforceable.

    Attributes:
        unit_id: The change ticket, supplied requirement or container.
        impact_nodes: How many distinct nodes the unit reaches
            (`R-310-144`'s count, never the path count).
        layers_crossed: How many container layers the impact spans.
        containers: How many distinct containers are touched.
        architecture_decision_requested: Whether any qualification on the
            unit came back `architecture-decision-required` (`R-310-148`).
        upstream_unaccepted: Whether the unit's upstream is not yet
            accepted, which makes its output speculative (`R-310-177`).
    """

    unit_id: str
    impact_nodes: int = 0
    layers_crossed: int = 0
    containers: int = 0
    architecture_decision_requested: bool = False
    upstream_unaccepted: bool = False


def classify(features: UnitFeatures) -> EffortClass:
    """Return the effort class of one unit, from its features alone.

    The order of the tests is the order of severity, and the first match
    wins: an explicitly requested architecture decision outranks any
    measurement, because a human has already said this needs thought.

    Args:
        features: The observed facts about the unit.

    Returns:
        The effort class.
    """
    if features.architecture_decision_requested:
        return EffortClass.REFLECTION_REQUIRED
    if features.layers_crossed >= DEEP_CASCADE_THRESHOLD:
        return EffortClass.REFLECTION_REQUIRED
    if features.impact_nodes > WIDE_IMPACT_THRESHOLD:
        return EffortClass.ARBITRATION_REQUIRED
    if features.containers > 1:
        return EffortClass.ARBITRATION_REQUIRED
    return EffortClass.BATCHABLE


def proposed_mode(effort: EffortClass) -> ExecutionMode:
    """Return the execution mode this effort class warrants (`R-310-172`).

    Only `batchable` runs through. Both other classes gate per unit,
    because both mean a human has to form a view before the next unit is
    touched — which is the definition of a gate.
    """
    return (
        ExecutionMode.END_TO_END
        if effort is EffortClass.BATCHABLE
        else ExecutionMode.STEP_BY_STEP
    )


def estimate(
    features: tuple[UnitFeatures, ...], mode: ExecutionMode
) -> StepEstimate:
    """Return the estimate for a step covering `features`.

    Args:
        features: The units the step treats.
        mode: Its execution mode, which decides whether a duration can be
            estimated at all.

    Returns:
        Tokens, euro, minutes and review items.
    """
    tokens = sum(
        TOKENS_PER_OBJECT + TOKENS_PER_IMPACT_NODE * unit.impact_nodes
        for unit in features
    )
    review_items = sum(
        REVIEW_ITEMS_PER_UNIT + unit.impact_nodes for unit in features
    )
    # A gated step's duration is set by reviewer availability, which this
    # module cannot observe. E-310-004 requires zero there rather than an
    # invented number, and the model enforces it.
    duration = (
        round(MINUTES_PER_UNIT * len(features))
        if mode is ExecutionMode.END_TO_END
        else 0
    )
    return StepEstimate(
        tokens=tokens,
        cost_eur=round(tokens * EUR_PER_MILLION_TOKENS / 1_000_000, 2),
        duration_min=duration,
        review_items=review_items,
    )


def explain(features: UnitFeatures) -> str:
    """Return why this unit got the class it did, in checkable terms.

    A reviewer ratifying a plan can verify every clause of this against
    the graph — which is the difference between a priced decision and a
    number to trust.
    """
    effort = classify(features)
    if features.architecture_decision_requested:
        reason = "an architecture decision was requested on it (R-310-148)"
    elif features.layers_crossed >= DEEP_CASCADE_THRESHOLD:
        reason = (
            f"its impact crosses {features.layers_crossed} container layers "
            f"(threshold {DEEP_CASCADE_THRESHOLD})"
        )
    elif features.impact_nodes > WIDE_IMPACT_THRESHOLD:
        reason = (
            f"it impacts {features.impact_nodes} nodes "
            f"(threshold {WIDE_IMPACT_THRESHOLD})"
        )
    elif features.containers > 1:
        reason = f"it touches {features.containers} containers"
    else:
        reason = (
            f"it impacts {features.impact_nodes} nodes in "
            f"{features.containers} container(s), below every threshold"
        )
    return f"{features.unit_id}: {effort.value} — {reason}"


def group_by_effort(
    features: tuple[UnitFeatures, ...],
) -> dict[EffortClass, tuple[UnitFeatures, ...]]:
    """Partition units by effort class, for one step per class.

    Grouping rather than one step per unit is what makes a 30 000-unit
    batch ratifiable: a reviewer approves three steps with three
    estimates, not thirty thousand.
    """
    grouped: dict[EffortClass, list[UnitFeatures]] = {}
    for unit in features:
        grouped.setdefault(classify(unit), []).append(unit)
    # Ordered by severity so the plan reads from cheap to expensive.
    order = (
        EffortClass.BATCHABLE,
        EffortClass.ARBITRATION_REQUIRED,
        EffortClass.REFLECTION_REQUIRED,
    )
    return {
        effort: tuple(grouped[effort]) for effort in order if effort in grouped
    }
