# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/models.py
# Description: The negotiated treatment plan — 310-SPEC §4.9, E-310-004
#              (R-310-170 … R-310-176).
#
#              A PLAN CANNOT BE RATIFIED WITHOUT AN ESTIMATE. R-310-171
#              lists what a plan declares; the model refuses a step with
#              no estimate rather than treating the field as optional,
#              because the whole purpose of ratification is to stop
#              "process everything end to end" from being a blank cheque
#              on the LLM budget and on the reviewer's time. An
#              unestimated step is that blank cheque with extra steps.
#
#              THE REVIEW-ITEM COUNT IS NOT OPTIONAL EITHER, and it is the
#              figure `R-310-171`'s rationale singles out: reviewer
#              capacity binds before budget does. A plan that estimates
#              tokens but not review items is estimating the cheap
#              resource.
#
#              RATIFICATION IS A RECORD, NOT A MESSAGE. R-310-175 is
#              explicit that it must exist independently of the
#              conversation in which it was expressed — a transcript is
#              not a record. So `Ratification` is its own object carrying
#              actor, instant and the PLAN VERSION it approved, and a plan
#              amended afterwards does not inherit it: `is_ratified`
#              compares versions, so an amendment silently invalidating
#              its own approval is impossible.
#
#              DURATION IS ZERO FOR A STEP-BY-STEP STEP, per E-310-004,
#              and that is a validated invariant rather than a convention:
#              the duration of a gated step is set by reviewer
#              availability, which the platform cannot estimate. Claiming
#              a number there would be inventing one.
#
# @relation implements:R-310-170
# @relation implements:R-310-171
# @relation implements:R-310-172
# @relation implements:R-310-173
# @relation implements:R-310-175
# @relation implements:R-310-176
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Shortest rationale accepted when a plan is amended mid-flight.
MIN_RATIONALE = 12


class EffortClass(StrEnum):
    """How much human engagement a step needs (`E-310-004`).

    Closed set. The classification drives the proposed execution mode, so
    a fourth label would change gating behaviour without a spec change.
    """

    BATCHABLE = "batchable"
    ARBITRATION_REQUIRED = "arbitration-required"
    REFLECTION_REQUIRED = "reflection-required"


class ExecutionMode(StrEnum):
    """Whether a step runs through or gates per unit (`R-310-172`)."""

    END_TO_END = "end-to-end"
    STEP_BY_STEP = "step-by-step"


class BatchKind(StrEnum):
    """What a plan's batch is made of."""

    CHANGE_SET = "change_set"
    SUPPLIED_REQUIREMENTS = "supplied_requirements"
    CONTAINER_AUTHORING = "container_authoring"


class StepState(StrEnum):
    """Where one step of a ratified plan stands.

    `SUSPENDED` is distinct from `FAILED`: a step that stopped because it
    passed its estimated cost did not go wrong, it went over
    (`R-310-174`), and the user's choice is to raise the estimate or
    re-split the step — not to debug it.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"


class Batch(BaseModel):
    """What one plan concerns (`R-310-171`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BatchKind
    source: str = Field(min_length=1)
    count: int = Field(ge=0)


class StepEstimate(BaseModel):
    """The four figures a step must declare before it may be ratified.

    `review_items` carries no default on purpose. Reviewer capacity binds
    before budget does (`R-310-171`'s rationale), so a step that forgot
    to estimate it has estimated the wrong resource — and a default of
    zero would read as "this generates no review work", which is the one
    claim a plan must never make silently.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tokens: int = Field(ge=0)
    cost_eur: float = Field(ge=0.0)
    duration_min: int = Field(ge=0)
    review_items: int = Field(ge=0)


class PlanStep(BaseModel):
    """One step of a treatment plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str = Field(min_length=1)
    scope: tuple[str, ...]
    """The identifiers this step treats — change tickets, supplied
    requirements or containers depending on the batch kind. Enumerated
    rather than counted so `R-310-173`'s re-split can partition it and so
    a completed step names what it actually touched."""
    effort: EffortClass
    mode: ExecutionMode
    estimate: StepEstimate
    state: StepState = StepState.PENDING
    suspended_reason: str | None = None

    @model_validator(mode="after")
    def _is_coherent(self) -> PlanStep:
        """Refuse a step whose declared shape could not arise."""
        if not self.scope:
            raise ValueError(
                f"step {self.step_id!r} treats nothing; an empty step consumes "
                "a gate and a ratification for no work"
            )
        if len(set(self.scope)) != len(self.scope):
            raise ValueError(
                f"step {self.step_id!r} lists an identifier twice; the scope is "
                "what the step reports having treated, so a duplicate would "
                "double-count it"
            )
        if (
            self.mode is ExecutionMode.STEP_BY_STEP
            and self.estimate.duration_min != 0
        ):
            raise ValueError(
                f"step {self.step_id!r} is gated per unit, so its duration is "
                "set by reviewer availability and the platform cannot estimate "
                "it; E-310-004 requires zero rather than an invented number"
            )
        if (self.suspended_reason is not None) != (
            self.state is StepState.SUSPENDED
        ):
            raise ValueError(
                f"step {self.step_id!r}: a suspension records why it suspended, "
                "and only a suspended step has one (R-310-174)"
            )
        return self

    @property
    def unit_count(self) -> int:
        """How many units this step treats."""
        return len(self.scope)

    @property
    def is_terminal(self) -> bool:
        """True when this step will not run again without an amendment."""
        return self.state in (StepState.COMPLETED, StepState.FAILED)


class Ratification(BaseModel):
    """The decision that execution may begin (`R-310-175`).

    Attached to the run and carrying the plan version it approved, so it
    survives independently of the conversation that produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_version: int = Field(ge=1)
    actor: str = Field(min_length=1)
    at: datetime


class Amendment(BaseModel):
    """One mid-flight change to a ratified plan (`R-310-173`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_version: int = Field(ge=1)
    to_version: int = Field(ge=2)
    rationale: str = Field(min_length=MIN_RATIONALE)
    actor: str = Field(min_length=1)
    at: datetime
    replaced_step_id: str = Field(min_length=1)
    into_step_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _advances(self) -> Amendment:
        if self.to_version <= self.from_version:
            raise ValueError("an amendment advances the plan version")
        if len(self.into_step_ids) < 2:
            raise ValueError(
                "re-splitting produces at least two steps; replacing one step "
                "with one step is an edit of the step, not an amendment"
            )
        return self


class TreatmentPlan(BaseModel):
    """A proposed or ratified plan of work (`E-310-004`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    batch: Batch
    steps: tuple[PlanStep, ...]
    proposed_by: str = Field(min_length=1)
    proposed_at: datetime
    ratification: Ratification | None = None
    amendments: tuple[Amendment, ...] = ()

    @model_validator(mode="after")
    def _is_coherent(self) -> TreatmentPlan:
        """Refuse a plan that could not have arisen from a real negotiation."""
        if not self.steps:
            raise ValueError(
                "a plan with no steps cannot be ratified into anything; "
                "R-310-171 requires a decomposition"
            )
        ids = [step.step_id for step in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("step identifiers are unique within a plan")

        seen: dict[str, str] = {}
        for step in self.steps:
            for unit in step.scope:
                if unit in seen:
                    raise ValueError(
                        f"{unit!r} is in both {seen[unit]!r} and "
                        f"{step.step_id!r}; treating one unit twice double-counts "
                        "its cost and its review items"
                    )
                seen[unit] = step.step_id

        if self.ratification is not None and (
            self.ratification.plan_version > self.version
        ):
            raise ValueError(
                "a ratification cannot approve a plan version that does not "
                "exist yet"
            )
        if self.amendments and self.amendments[-1].to_version != self.version:
            raise ValueError(
                "the last amendment's target version is the plan's version; "
                "otherwise the amendment trail does not account for how the "
                "plan reached the version it claims"
            )
        return self

    @property
    def is_ratified(self) -> bool:
        """True when THIS version of the plan carries a ratification.

        Version-compared rather than presence-checked: `R-310-173` lets a
        plan be amended during execution, and an amendment that inherited
        its predecessor's approval would let work proceed on terms nobody
        agreed to.
        """
        return (
            self.ratification is not None
            and self.ratification.plan_version == self.version
        )

    @property
    def total_estimate(self) -> StepEstimate:
        """The plan's estimate: the sum over its steps."""
        return StepEstimate(
            tokens=sum(step.estimate.tokens for step in self.steps),
            cost_eur=round(sum(step.estimate.cost_eur for step in self.steps), 2),
            duration_min=sum(step.estimate.duration_min for step in self.steps),
            review_items=sum(step.estimate.review_items for step in self.steps),
        )

    @property
    def unit_count(self) -> int:
        """How many units the plan treats in total."""
        return sum(step.unit_count for step in self.steps)

    @property
    def gated_step_ids(self) -> tuple[str, ...]:
        """The steps that stop for a human between units (`R-310-172`)."""
        return tuple(
            step.step_id
            for step in self.steps
            if step.mode is ExecutionMode.STEP_BY_STEP
        )

    @property
    def completed_step_ids(self) -> tuple[str, ...]:
        """The steps an amendment must not disturb (`R-310-173`)."""
        return tuple(
            step.step_id
            for step in self.steps
            if step.state is StepState.COMPLETED
        )

    @property
    def suspended_step_ids(self) -> tuple[str, ...]:
        """The steps that stopped on an overrun (`R-310-174`)."""
        return tuple(
            step.step_id
            for step in self.steps
            if step.state is StepState.SUSPENDED
        )

    @property
    def is_complete(self) -> bool:
        """True when every step reached a terminal state."""
        return all(step.is_terminal for step in self.steps)

    def step(self, step_id: str) -> PlanStep:
        """Return one step.

        Raises:
            KeyError: When the plan has no such step.
        """
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"{step_id!r} is not a step of {self.plan_id!r}")


class TreatmentReport(BaseModel):
    """What a completed plan did (`R-310-176`, `E-310-007`).

    The closure artefact, and the document exported when an issuing party
    asks for progress against its supplied requirements.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    plan_version: int = Field(ge=1)
    generated_at: datetime
    requirements_processed: tuple[str, ...] = ()
    containers_modified: tuple[str, ...] = ()
    objects_created: tuple[str, ...] = ()
    review_outcomes: dict[str, int] = Field(default_factory=dict)
    """Count per review state, so "examined and accepted" and
    "auto-accepted by cluster review" are never summed (`R-310-007`)."""
    coverage_before: int = Field(default=0, ge=0)
    coverage_after: int = Field(default=0, ge=0)
    remaining_gaps: tuple[str, ...] = ()
    returns_awaiting_arbitration: tuple[str, ...] = ()

    @property
    def coverage_delta(self) -> int:
        """How many more requirements are answered than before."""
        return self.coverage_after - self.coverage_before

    @property
    def is_clean(self) -> bool:
        """True when the plan left nothing outstanding."""
        return not self.remaining_gaps and not self.returns_awaiting_arbitration


# ---------------------------------------------------------------------------
# REST bodies
# ---------------------------------------------------------------------------


class ProposeRequest(BaseModel):
    """Ask for a treatment plan over a batch."""

    model_config = ConfigDict(extra="forbid")

    batch: Batch
    units: tuple[str, ...] = ()
    """The identifiers to treat. Supplied explicitly so the plan's scope is
    what the caller asked for, not what a query happened to return at
    proposal time."""


class RatifyRequest(BaseModel):
    """Ratify the plan as it currently stands (`R-310-170`)."""

    model_config = ConfigDict(extra="forbid")

    plan_version: int = Field(ge=1)
    """Echoed back so a plan amended between display and ratification is
    refused rather than approved unseen."""


class AmendRequest(BaseModel):
    """Re-split one step of a ratified plan (`R-310-173`)."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    partitions: tuple[tuple[str, ...], ...]
    rationale: str = Field(min_length=MIN_RATIONALE)


class ConsumptionRequest(BaseModel):
    """Report what a running step has consumed so far (`R-310-174`)."""

    model_config = ConfigDict(extra="forbid")

    tokens: int = Field(default=0, ge=0)
    objects: int = Field(default=0, ge=0)
    review_items: int = Field(default=0, ge=0)


class ReportRequest(BaseModel):
    """The observed figures a treatment report is assembled from.

    Supplied by the caller rather than recomputed here: the coverage
    figures and review outcomes are owned by the coverage and object
    surfaces, and re-deriving them in a second place is how two answers to
    one question appear.
    """

    model_config = ConfigDict(extra="forbid")

    containers_modified: tuple[str, ...] = ()
    objects_created: tuple[str, ...] = ()
    review_outcomes: dict[str, int] = Field(default_factory=dict)
    coverage_before: int = Field(default=0, ge=0)
    coverage_after: int = Field(default=0, ge=0)
    remaining_gaps: tuple[str, ...] = ()
    returns_awaiting_arbitration: tuple[str, ...] = ()


class PlanListResponse(BaseModel):
    """A page of treatment plans."""

    model_config = ConfigDict(extra="forbid")

    plans: tuple[TreatmentPlan, ...]
    count: int = Field(ge=0)
