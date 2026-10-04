# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/service.py
# Description: Negotiated piloting — 310-SPEC §4.9 (R-310-170 … R-310-176).
#
#              THE RULE THIS MODULE EXISTS FOR. R-310-170: an agent SHALL
#              NOT begin execution until a human has ratified the plan. So
#              `begin_step` refuses on an unratified plan, and "ratified"
#              is version-compared — a plan amended after approval is
#              unratified again (`R-310-173` + `R-310-170` read together).
#              Without the version comparison, amendment would be a way to
#              change the terms of work already approved, which is the one
#              thing ratification exists to prevent.
#
#              RATIFICATION ECHOES THE VERSION BACK. `ratify` takes the
#              version the caller believes they are approving and refuses a
#              mismatch. A plan amended between being displayed and being
#              approved would otherwise be approved unseen, which is the
#              same failure as the ticket-closure gate accepting evidence
#              nobody looked at.
#
#              AMENDMENT SPARES COMPLETED WORK (R-310-173). Re-splitting a
#              step must not invalidate steps already completed, so an
#              amendment targeting a completed step is refused outright
#              rather than silently reopening it, and the partition must
#              exactly re-cover the replaced step's scope — no unit lost,
#              none invented.
#
#              SUSPENSION IS NOT FAILURE (R-310-174). `record_consumption`
#              evaluates the budget guard and suspends, carrying the
#              breach report so the user can raise the estimate or
#              re-split. A step that went over did not go wrong.
#
# @relation implements:R-310-170
# @relation implements:R-310-171
# @relation implements:R-310-172
# @relation implements:R-310-173
# @relation implements:R-310-174
# @relation implements:R-310-175
# @relation implements:R-310-176
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from .budget import DEFAULT_MARGIN, Consumption, check
from .estimation import UnitFeatures, estimate, group_by_effort, proposed_mode
from .models import (
    Amendment,
    Batch,
    EffortClass,
    PlanStep,
    Ratification,
    StepState,
    TreatmentPlan,
    TreatmentReport,
)
from .repository import ExecutionRepository
from .storage import ExecutionStorage

#: Severity order used when an amended partition spans effort classes: the
#: step takes the most demanding class present, because a step gated for
#: its hardest unit is safe while one gated for its easiest is not. Keyed by
#: the enum, not by its values: a `StrEnum` happens to hash like its string,
#: and relying on that makes the lookup silently wrong the day the enum
#: stops being a `StrEnum`.
_SEVERITY: dict[EffortClass, int] = {
    EffortClass.BATCHABLE: 0,
    EffortClass.ARBITRATION_REQUIRED: 1,
    EffortClass.REFLECTION_REQUIRED: 2,
}

#: (project_id, unit_ids) -> the measurable features of each unit. Injected
#: because the features come from the coverage graph and the change
#: tickets, and binding them here would make planning untestable without a
#: database — the same reason `coverage/service.py` injects its lookup.
FeatureLookup = Callable[[str, tuple[str, ...]], Awaitable[Sequence[UnitFeatures]]]


class PlanRefusedError(RuntimeError):
    """Raised when a planning or execution step cannot proceed as requested."""


class PlanNotFoundError(RuntimeError):
    """Raised when a plan is addressed that was never proposed."""


class NotRatifiedError(RuntimeError):
    """Raised when execution is attempted on an unratified plan (`R-310-170`)."""


class ExecutionService:
    """Propose, ratify, amend and execute treatment plans.

    Args:
        storage: Source-of-truth persistence for plans and reports.
        repository: The queryable index.
        features_of: Lookup of the measurable features behind an estimate.
        margin: Permitted fractional overshoot before a step suspends.
    """

    def __init__(
        self,
        storage: ExecutionStorage,
        repository: ExecutionRepository,
        features_of: FeatureLookup,
        margin: float = DEFAULT_MARGIN,
    ) -> None:
        self._storage = storage
        self._repo = repository
        self._features_of = features_of
        self._margin = margin

    # ------------------------------------------------------------------
    # Proposal — R-310-171 / R-310-172
    # ------------------------------------------------------------------

    async def propose(
        self,
        project_id: str,
        plan_id: str,
        *,
        batch: Batch,
        units: tuple[str, ...],
        actor: str,
        now: datetime | None = None,
    ) -> TreatmentPlan:
        """Propose a plan: one step per effort class, each with its estimate.

        Grouping by effort class rather than one step per unit is what
        makes a 30 000-unit batch ratifiable at all — a reviewer approves
        three priced steps, not thirty thousand.

        Raises:
            PlanRefusedError: When the batch is empty or a plan already
                exists under that identifier.
        """
        if not units:
            raise PlanRefusedError(
                "a plan over nothing cannot be ratified into anything; "
                "R-310-171 requires a decomposition of a batch"
            )
        if await self._storage.get_plan(project_id, plan_id) is not None:
            raise PlanRefusedError(
                f"{plan_id!r} already exists; amend it (R-310-173) rather than "
                "re-proposing, so its ratification trail survives"
            )

        features = tuple(await self._features_of(project_id, units))
        missing = set(units) - {unit.unit_id for unit in features}
        if missing:
            raise PlanRefusedError(
                f"no features for {sorted(missing)}; a step cannot be estimated "
                "from units the graph does not know, and an unestimated step is "
                "the blank cheque R-310-171 exists to prevent"
            )

        steps: list[PlanStep] = []
        for ordinal, (effort, group) in enumerate(
            group_by_effort(features).items(), start=1
        ):
            mode = proposed_mode(effort)
            steps.append(
                PlanStep(
                    step_id=f"st{ordinal}",
                    scope=tuple(unit.unit_id for unit in group),
                    effort=effort,
                    mode=mode,
                    estimate=estimate(group, mode),
                )
            )

        plan = TreatmentPlan(
            plan_id=plan_id,
            project_id=project_id,
            batch=batch,
            steps=tuple(steps),
            proposed_by=actor,
            proposed_at=now or datetime.now(UTC),
        )
        await self._persist(plan)
        return plan

    # ------------------------------------------------------------------
    # Ratification — R-310-170 / R-310-175
    # ------------------------------------------------------------------

    async def ratify(
        self,
        project_id: str,
        plan_id: str,
        *,
        plan_version: int,
        actor: str,
        now: datetime | None = None,
    ) -> TreatmentPlan:
        """Record that a human approved the plan as it stands.

        Args:
            plan_version: The version the caller believes they are
                approving. A mismatch is refused rather than reconciled: a
                plan amended between display and ratification would
                otherwise be approved unseen.

        Raises:
            PlanNotFoundError: When no plan was proposed.
            PlanRefusedError: When the version does not match, or the
                current version is already ratified.
        """
        plan = await self._load(project_id, plan_id)
        if plan_version != plan.version:
            raise PlanRefusedError(
                f"{plan_id!r} is at version {plan.version}, not {plan_version}; "
                "it was amended since you read it, so ratifying now would "
                "approve terms you have not seen (R-310-170)"
            )
        if plan.is_ratified:
            raise PlanRefusedError(
                f"{plan_id!r} version {plan.version} is already ratified by "
                f"{plan.ratification.actor if plan.ratification else '?'}"
            )
        ratified = plan.model_copy(
            update={
                "ratification": Ratification(
                    plan_version=plan.version,
                    actor=actor,
                    at=now or datetime.now(UTC),
                )
            }
        )
        await self._persist(ratified)
        return ratified

    # ------------------------------------------------------------------
    # Amendment — R-310-173
    # ------------------------------------------------------------------

    async def amend(
        self,
        project_id: str,
        plan_id: str,
        *,
        step_id: str,
        partitions: tuple[tuple[str, ...], ...],
        rationale: str,
        actor: str,
        now: datetime | None = None,
    ) -> TreatmentPlan:
        """Re-split one step, leaving completed steps untouched.

        The partition must exactly re-cover the replaced step's scope. A
        partition that lost a unit would drop work the plan accounted for;
        one that invented a unit would smuggle in work nobody priced.

        Raises:
            PlanNotFoundError: When no plan was proposed.
            PlanRefusedError: When the step is completed, the partition
                does not re-cover its scope, or fewer than two parts were
                given.
        """
        plan = await self._load(project_id, plan_id)
        try:
            target = plan.step(step_id)
        except KeyError as exc:
            raise PlanRefusedError(str(exc)) from exc

        if target.state is StepState.COMPLETED:
            raise PlanRefusedError(
                f"{step_id!r} is completed; R-310-173 requires that re-splitting "
                "a step not invalidate work already done, so a completed step is "
                "not re-splittable"
            )
        if len(partitions) < 2:
            raise PlanRefusedError(
                "re-splitting produces at least two steps; replacing one step "
                "with one step is an edit, not an amendment (R-310-173)"
            )

        flattened = [unit for part in partitions for unit in part]
        if len(set(flattened)) != len(flattened):
            raise PlanRefusedError(
                "a unit appears in two partitions; treating it twice "
                "double-counts its cost and its review items"
            )
        if set(flattened) != set(target.scope):
            lost = sorted(set(target.scope) - set(flattened))
            added = sorted(set(flattened) - set(target.scope))
            raise PlanRefusedError(
                f"the partition does not re-cover {step_id!r}: lost {lost}, "
                f"added {added}. A lost unit drops work the plan accounted for; "
                "an added one smuggles in work nobody priced"
            )

        features = tuple(await self._features_of(project_id, tuple(flattened)))
        by_id = {unit.unit_id: unit for unit in features}
        missing = set(flattened) - set(by_id)
        if missing:
            raise PlanRefusedError(f"no features for {sorted(missing)}")

        moment = now or datetime.now(UTC)
        new_version = plan.version + 1
        replacements: list[PlanStep] = []
        new_ids: list[str] = []
        for ordinal, part in enumerate(partitions, start=1):
            group = tuple(by_id[unit] for unit in part)
            # Re-classify: the whole reason to amend is that the original
            # classification was wrong, so carrying it forward would
            # preserve the bad estimate the amendment exists to fix.
            effort = max(
                group_by_effort(group),
                key=lambda e: _SEVERITY[e],
            )
            mode = proposed_mode(effort)
            new_id = f"{step_id}.{ordinal}"
            new_ids.append(new_id)
            replacements.append(
                PlanStep(
                    step_id=new_id,
                    scope=part,
                    effort=effort,
                    mode=mode,
                    estimate=estimate(group, mode),
                )
            )

        steps = tuple(
            replacement
            for step in plan.steps
            for replacement in (replacements if step.step_id == step_id else [step])
        )
        amended = plan.model_copy(
            update={
                "version": new_version,
                "steps": steps,
                "amendments": (
                    *plan.amendments,
                    Amendment(
                        from_version=plan.version,
                        to_version=new_version,
                        rationale=rationale,
                        actor=actor,
                        at=moment,
                        replaced_step_id=step_id,
                        into_step_ids=tuple(new_ids),
                    ),
                ),
            }
        )
        await self._persist(amended)
        return amended

    # ------------------------------------------------------------------
    # Execution — R-310-170 / R-310-174
    # ------------------------------------------------------------------

    async def begin_step(
        self, project_id: str, plan_id: str, *, step_id: str
    ) -> TreatmentPlan:
        """Mark a step running, refusing an unratified plan (`R-310-170`).

        Raises:
            PlanNotFoundError: When no plan was proposed.
            NotRatifiedError: When the CURRENT version carries no
                ratification — including a plan amended past its approval.
            PlanRefusedError: When the step is already terminal.
        """
        plan = await self._load(project_id, plan_id)
        if not plan.is_ratified:
            raise NotRatifiedError(
                f"{plan_id!r} version {plan.version} is not ratified; an agent "
                "SHALL NOT begin execution until a human has ratified the plan "
                "(R-310-170)"
            )
        return await self._set_state(plan, step_id, StepState.RUNNING)

    async def record_consumption(
        self,
        project_id: str,
        plan_id: str,
        *,
        step_id: str,
        consumed: Consumption,
    ) -> TreatmentPlan:
        """Evaluate the budget guard and suspend on an overrun (`R-310-174`).

        Returns the plan unchanged when the step is within every allowance.

        Raises:
            PlanNotFoundError: When no plan was proposed.
            PlanRefusedError: When the plan has no such step.
        """
        plan = await self._load(project_id, plan_id)
        try:
            step = plan.step(step_id)
        except KeyError as exc:
            raise PlanRefusedError(str(exc)) from exc

        verdict = check(step.estimate, consumed, margin=self._margin)
        if verdict.may_continue:
            return plan
        return await self._set_state(
            plan, step_id, StepState.SUSPENDED, reason=verdict.explain()
        )

    async def complete_step(
        self, project_id: str, plan_id: str, *, step_id: str
    ) -> TreatmentPlan:
        """Mark a step completed.

        Raises:
            PlanNotFoundError: When no plan was proposed.
            PlanRefusedError: When the step is already terminal.
        """
        plan = await self._load(project_id, plan_id)
        return await self._set_state(plan, step_id, StepState.COMPLETED)

    async def fail_step(
        self, project_id: str, plan_id: str, *, step_id: str
    ) -> TreatmentPlan:
        """Mark a step failed — an error, not an overrun (`R-310-174`)."""
        plan = await self._load(project_id, plan_id)
        return await self._set_state(plan, step_id, StepState.FAILED)

    # ------------------------------------------------------------------
    # Reads and reporting
    # ------------------------------------------------------------------

    async def get_plan(self, project_id: str, plan_id: str) -> TreatmentPlan:
        """Return one plan.

        Raises:
            PlanNotFoundError: When none was proposed.
        """
        return await self._load(project_id, plan_id)

    async def get_plan_version(
        self, project_id: str, plan_id: str, version: int
    ) -> TreatmentPlan:
        """Return one historical plan version — what was actually approved.

        Raises:
            PlanNotFoundError: When that version was never stored.
        """
        plan = await self._storage.get_plan_version(project_id, plan_id, version)
        if plan is None:
            raise PlanNotFoundError(
                f"{plan_id!r} has no stored version {version}"
            )
        return plan

    async def list_plans(
        self,
        project_id: str,
        *,
        awaiting_ratification: bool = False,
        active_only: bool = False,
    ) -> tuple[TreatmentPlan, ...]:
        """Return a project's plans, oldest first."""
        rows = await self._repo.list_plans(
            project_id,
            awaiting_ratification=awaiting_ratification,
            active_only=active_only,
        )
        plans: list[TreatmentPlan] = []
        for row in rows:
            plan = await self._storage.get_plan(project_id, row["plan_id"])
            if plan is not None:
                plans.append(plan)
        return tuple(plans)

    async def get_report(
        self, project_id: str, plan_id: str
    ) -> TreatmentReport | None:
        """Return a plan's treatment report, or None when not yet produced."""
        return await self._storage.get_report(project_id, plan_id)

    async def produce_report(
        self,
        project_id: str,
        plan_id: str,
        *,
        containers_modified: tuple[str, ...] = (),
        objects_created: tuple[str, ...] = (),
        review_outcomes: dict[str, int] | None = None,
        coverage_before: int = 0,
        coverage_after: int = 0,
        remaining_gaps: tuple[str, ...] = (),
        returns_awaiting_arbitration: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> TreatmentReport:
        """Produce the treatment report of a completed plan (`R-310-176`).

        Refuses an incomplete plan: a report naming what was processed
        while steps are still pending would assert completion that has not
        happened, and the report is the artefact an issuing party reads.

        Raises:
            PlanNotFoundError: When no plan was proposed.
            PlanRefusedError: When the plan has steps outstanding.
        """
        plan = await self._load(project_id, plan_id)
        if not plan.is_complete:
            outstanding = tuple(
                step.step_id for step in plan.steps if not step.is_terminal
            )
            raise PlanRefusedError(
                f"{plan_id!r} still has {list(outstanding)} outstanding; a "
                "treatment report is the record of a COMPLETED plan "
                "(R-310-176)"
            )
        report = TreatmentReport(
            plan_id=plan_id,
            project_id=project_id,
            plan_version=plan.version,
            generated_at=now or datetime.now(UTC),
            requirements_processed=tuple(
                unit
                for step in plan.steps
                if step.state is StepState.COMPLETED
                for unit in step.scope
            ),
            containers_modified=containers_modified,
            objects_created=objects_created,
            review_outcomes=review_outcomes or {},
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            remaining_gaps=remaining_gaps,
            returns_awaiting_arbitration=returns_awaiting_arbitration,
        )
        await self._storage.put_report(report)
        return report

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load(self, project_id: str, plan_id: str) -> TreatmentPlan:
        plan = await self._storage.get_plan(project_id, plan_id)
        if plan is None:
            raise PlanNotFoundError(
                f"no treatment plan {plan_id!r} in project {project_id!r}"
            )
        return plan

    async def _set_state(
        self,
        plan: TreatmentPlan,
        step_id: str,
        state: StepState,
        *,
        reason: str | None = None,
    ) -> TreatmentPlan:
        try:
            current = plan.step(step_id)
        except KeyError as exc:
            raise PlanRefusedError(str(exc)) from exc
        if current.is_terminal:
            raise PlanRefusedError(
                f"{step_id!r} is {current.state.value}; a terminal step does not "
                "change state without an amendment (R-310-173)"
            )
        updated = current.model_copy(
            update={"state": state, "suspended_reason": reason}
        )
        changed = plan.model_copy(
            update={
                "steps": tuple(
                    updated if step.step_id == step_id else step
                    for step in plan.steps
                )
            }
        )
        await self._persist(changed)
        return changed

    async def _persist(self, plan: TreatmentPlan) -> None:
        await self._storage.put_plan(plan)
        await self._repo.put_plan(plan)


