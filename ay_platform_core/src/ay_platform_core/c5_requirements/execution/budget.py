# =============================================================================
# File: budget.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/budget.py
# Description: The overrun guard — 310-SPEC §4.9 (R-310-174).
#
#              "A silent overrun is how a budget is consumed without
#              anyone noticing." The guard therefore reports a BREACH, not
#              a boolean: a step that suspends must be able to tell the
#              user which figure went over, by how much, and against what
#              — otherwise the remedy (raise the estimate, or re-split the
#              step under R-310-173) is a guess.
#
#              IT SUSPENDS, IT DOES NOT FAIL. R-310-174 says "suspend and
#              return to the user rather than continue". A step over its
#              estimate did not go wrong; it went over. The distinction is
#              load-bearing because the two have different remedies, which
#              is why `StepState` separates them.
#
#              THE MARGIN IS CONFIGURED, NOT HARD-CODED, and it is a
#              FRACTION rather than an absolute: an absolute margin that
#              suits a 300-token step is noise on a four-million-token one.
#
#              WHY THREE FIGURES AND NOT FOUR. R-310-174 names token cost,
#              object count and review-item count — not duration. Duration
#              is wall-clock, and suspending a run because it took longer
#              than guessed would stop work that is proceeding correctly.
#              The three guarded figures are all ones where overrunning
#              means more was consumed or more was asked of a reviewer.
#
# @relation implements:R-310-174
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import StepEstimate

#: Fraction by which a figure may exceed its estimate before the step
#: suspends. 0.25 leaves room for an imperfect estimator without letting a
#: four-million-token step quietly become six.
DEFAULT_MARGIN = 0.25


class BudgetFigure(StrEnum):
    """The figures `R-310-174` guards.

    Duration is deliberately absent: it is wall-clock, and suspending work
    that is proceeding correctly because it is slower than guessed would
    be a worse outcome than finishing late.
    """

    TOKENS = "tokens"
    OBJECTS = "objects"
    REVIEW_ITEMS = "review_items"


@dataclass(frozen=True, slots=True)
class Consumption:
    """What a step has actually consumed so far."""

    tokens: int = 0
    objects: int = 0
    review_items: int = 0


@dataclass(frozen=True, slots=True)
class Breach:
    """One figure that passed its allowance."""

    figure: BudgetFigure
    estimated: int
    allowed: int
    actual: int

    @property
    def overrun_fraction(self) -> float:
        """By what fraction the estimate was exceeded.

        Returns 0.0 when the estimate was zero, rather than dividing: an
        estimate of zero that consumed anything is already reported by
        `allowed` being zero, and a division would raise inside a guard
        whose whole job is not to blow up.
        """
        if self.estimated <= 0:
            return 0.0
        return round((self.actual - self.estimated) / self.estimated, 3)

    def explain(self) -> str:
        """Return a reader-facing account of this breach."""
        return (
            f"{self.figure.value}: estimated {self.estimated:,}, "
            f"allowed {self.allowed:,}, consumed {self.actual:,}"
        )


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    """Whether a step may continue, and why not if it may not."""

    breaches: tuple[Breach, ...] = ()

    @property
    def may_continue(self) -> bool:
        """True when nothing has passed its allowance."""
        return not self.breaches

    def explain(self) -> str:
        """Return the suspension reason, suitable for `PlanStep`.

        Every breach is listed, not just the first: a step suspended for
        tokens that is also over on review items should not suspend twice.
        """
        if self.may_continue:
            return "within every allowance"
        return "; ".join(breach.explain() for breach in self.breaches)


def allowance(estimated: int, margin: float = DEFAULT_MARGIN) -> int:
    """Return the ceiling a figure may reach before the step suspends.

    Args:
        estimated: The ratified estimate for the figure.
        margin: Permitted fractional overshoot.

    Returns:
        The inclusive ceiling.

    Raises:
        ValueError: When the margin is negative.
    """
    if margin < 0:
        raise ValueError("margin cannot be negative")
    return int(estimated * (1.0 + margin))


def check(
    estimate: StepEstimate,
    consumed: Consumption,
    *,
    object_estimate: int | None = None,
    margin: float = DEFAULT_MARGIN,
) -> BudgetVerdict:
    """Return whether a step may continue (`R-310-174`).

    Args:
        estimate: The ratified estimate for the step.
        consumed: What it has consumed so far.
        object_estimate: Expected object count. Defaults to the review-item
            estimate, which is the closest proxy the plan carries — one
            review item per object touched is the normal case.
        margin: Permitted fractional overshoot per figure.

    Returns:
        A verdict listing every breached figure.
    """
    objects_expected = (
        estimate.review_items if object_estimate is None else object_estimate
    )
    candidates = (
        (BudgetFigure.TOKENS, estimate.tokens, consumed.tokens),
        (BudgetFigure.OBJECTS, objects_expected, consumed.objects),
        (BudgetFigure.REVIEW_ITEMS, estimate.review_items, consumed.review_items),
    )
    breaches = tuple(
        Breach(
            figure=figure,
            estimated=estimated,
            allowed=allowance(estimated, margin),
            actual=actual,
        )
        for figure, estimated, actual in candidates
        if actual > allowance(estimated, margin)
    )
    return BudgetVerdict(breaches=breaches)
