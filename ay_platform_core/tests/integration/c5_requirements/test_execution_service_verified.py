# =============================================================================
# File: test_execution_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_execution_service_verified.py
# Description: Integration tests for ExecutionService against real MinIO +
#              ArangoDB — 310-SPEC §4.9.
#
#              What this tier establishes that no lower one can:
#                - execution is refused on an unratified plan, AND on a plan
#                  amended past its ratification (R-310-170 + R-310-173 read
#                  together) — the second is the one a unit test on the
#                  model cannot reach, because it needs the amend path;
#                - ratifying a version the caller did not read is refused;
#                - an amendment leaves completed steps untouched and must
#                  re-cover exactly the scope it replaces;
#                - a step over its estimate SUSPENDS carrying the breach,
#                  and is not reported as failed (R-310-174);
#                - every plan version remains readable afterwards, which is
#                  what makes a ratification auditable (R-310-175).
#
# @relation validates:R-310-170
# @relation validates:R-310-171
# @relation validates:R-310-172
# @relation validates:R-310-173
# @relation validates:R-310-174
# @relation validates:R-310-175
# @relation validates:R-310-176
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.execution.budget import Consumption
from ay_platform_core.c5_requirements.execution.estimation import UnitFeatures
from ay_platform_core.c5_requirements.execution.models import (
    Batch,
    BatchKind,
    EffortClass,
    ExecutionMode,
    StepState,
)
from ay_platform_core.c5_requirements.execution.repository import (
    ExecutionRepository,
)
from ay_platform_core.c5_requirements.execution.service import (
    ExecutionService,
    NotRatifiedError,
    PlanNotFoundError,
    PlanRefusedError,
)
from ay_platform_core.c5_requirements.execution.storage import ExecutionStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
PID = "adas"
PLAN = "TP-0114"
OWNER = "o.mathieu"
AGENT = "agent:planner"
BATCH = Batch(kind=BatchKind.CHANGE_SET, source="CUST-2026-W14", count=4)
_WHY = "The batchable estimate was wrong by an order of magnitude."


class Features:
    """Injected feature lookup, driven by the test."""

    def __init__(self) -> None:
        self.table: dict[str, UnitFeatures] = {}

    def add(self, unit_id: str, **kwargs: object) -> None:
        self.table[unit_id] = UnitFeatures(unit_id=unit_id, **kwargs)  # type: ignore[arg-type]

    async def __call__(
        self, project_id: str, units: tuple[str, ...]
    ) -> Sequence[UnitFeatures]:
        return [self.table[unit] for unit in units if unit in self.table]


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[ExecutionService, Features, ExecutionStorage]]:
    db_name = f"c5_exec_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ExecutionRepository(db)
        repo._ensure_collections_sync()
        features = Features()
        storage = ExecutionStorage(c5_storage)
        yield ExecutionService(storage, repo, features), features, storage
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
def service(
    wired: tuple[ExecutionService, Features, ExecutionStorage],
) -> ExecutionService:
    return wired[0]


@pytest.fixture
def features(
    wired: tuple[ExecutionService, Features, ExecutionStorage],
) -> Features:
    return wired[1]


@pytest.fixture
def storage(
    wired: tuple[ExecutionService, Features, ExecutionStorage],
) -> ExecutionStorage:
    return wired[2]


def _four_batchable(features: Features) -> tuple[str, ...]:
    units = tuple(f"CT-{i:03d}" for i in range(1, 5))
    for unit in units:
        features.add(unit, impact_nodes=2, layers_crossed=1, containers=1)
    return units


async def _proposed(
    service: ExecutionService, features: Features, plan_id: str = PLAN
) -> tuple[str, ...]:
    units = _four_batchable(features)
    await service.propose(
        PID, plan_id, batch=BATCH, units=units, actor=AGENT, now=NOW
    )
    return units


async def _ratified(service: ExecutionService, features: Features) -> None:
    await _proposed(service, features)
    await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)


# ---------------------------------------------------------------------------
# Proposal — R-310-171 / R-310-172
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_proposal_groups_units_into_one_step_per_effort_class(
    service: ExecutionService, features: Features
) -> None:
    features.add("CT-001", impact_nodes=2, layers_crossed=1, containers=1)
    features.add("CT-002", impact_nodes=2, layers_crossed=1, containers=2)
    features.add("CT-003", impact_nodes=2, layers_crossed=4, containers=1)

    plan = await service.propose(
        PID,
        PLAN,
        batch=BATCH,
        units=("CT-001", "CT-002", "CT-003"),
        actor=AGENT,
        now=NOW,
    )
    assert len(plan.steps) == 3
    assert {step.effort for step in plan.steps} == set(EffortClass)


@pytest.mark.asyncio
async def test_every_step_carries_an_estimate_with_review_items(
    service: ExecutionService, features: Features
) -> None:
    await _proposed(service, features)
    plan = await service.get_plan(PID, PLAN)
    for step in plan.steps:
        assert step.estimate.tokens > 0
        assert step.estimate.review_items > 0


@pytest.mark.asyncio
async def test_a_batchable_step_is_proposed_end_to_end(
    service: ExecutionService, features: Features
) -> None:
    await _proposed(service, features)
    plan = await service.get_plan(PID, PLAN)
    assert plan.steps[0].mode is ExecutionMode.END_TO_END


@pytest.mark.asyncio
async def test_a_reflection_step_is_proposed_gated_with_no_duration(
    service: ExecutionService, features: Features
) -> None:
    features.add("CT-009", impact_nodes=2, layers_crossed=5, containers=1)
    plan = await service.propose(
        PID, PLAN, batch=BATCH, units=("CT-009",), actor=AGENT, now=NOW
    )
    assert plan.steps[0].mode is ExecutionMode.STEP_BY_STEP
    assert plan.steps[0].estimate.duration_min == 0


@pytest.mark.asyncio
async def test_a_proposal_over_nothing_is_refused(
    service: ExecutionService,
) -> None:
    with pytest.raises(PlanRefusedError, match="R-310-171"):
        await service.propose(
            PID, PLAN, batch=BATCH, units=(), actor=AGENT, now=NOW
        )


@pytest.mark.asyncio
async def test_a_unit_the_graph_does_not_know_is_refused_not_guessed(
    service: ExecutionService, features: Features
) -> None:
    """An unestimated step is the blank cheque R-310-171 exists to prevent."""
    features.add("CT-001", impact_nodes=1)
    with pytest.raises(PlanRefusedError, match="no features for"):
        await service.propose(
            PID, PLAN, batch=BATCH, units=("CT-001", "CT-404"), actor=AGENT, now=NOW
        )


@pytest.mark.asyncio
async def test_re_proposing_the_same_plan_id_is_refused(
    service: ExecutionService, features: Features
) -> None:
    """Re-proposing would discard the ratification trail."""
    await _proposed(service, features)
    with pytest.raises(PlanRefusedError, match="already exists"):
        await _proposed(service, features)


@pytest.mark.asyncio
async def test_an_unknown_plan_is_reported_missing(
    service: ExecutionService,
) -> None:
    with pytest.raises(PlanNotFoundError):
        await service.get_plan(PID, "TP-9999")


# ---------------------------------------------------------------------------
# Ratification — R-310-170 / R-310-175
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ratification_records_actor_instant_and_version(
    service: ExecutionService, features: Features
) -> None:
    await _proposed(service, features)
    plan = await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)
    assert plan.is_ratified is True
    assert plan.ratification is not None
    assert plan.ratification.actor == OWNER
    assert plan.ratification.at == NOW
    assert plan.ratification.plan_version == 1


@pytest.mark.asyncio
async def test_ratifying_a_version_the_caller_did_not_read_is_refused(
    service: ExecutionService, features: Features
) -> None:
    """Otherwise an amendment between display and approval is approved unseen."""
    await _proposed(service, features)
    with pytest.raises(PlanRefusedError, match="not 7"):
        await service.ratify(PID, PLAN, plan_version=7, actor=OWNER, now=NOW)


@pytest.mark.asyncio
async def test_ratifying_twice_is_refused(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    with pytest.raises(PlanRefusedError, match="already ratified"):
        await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)


# ---------------------------------------------------------------------------
# R-310-170 — execution needs ratification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_is_refused_on_an_unratified_plan(
    service: ExecutionService, features: Features
) -> None:
    await _proposed(service, features)
    with pytest.raises(NotRatifiedError, match="R-310-170"):
        await service.begin_step(PID, PLAN, step_id="st1")


@pytest.mark.asyncio
async def test_execution_proceeds_on_a_ratified_plan(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    plan = await service.begin_step(PID, PLAN, step_id="st1")
    assert plan.step("st1").state is StepState.RUNNING


@pytest.mark.asyncio
async def test_execution_is_refused_again_after_an_amendment(
    service: ExecutionService, features: Features
) -> None:
    """The case no model-level test reaches: approval does not survive amendment.

    Without this, amending would be a way to change the terms of work
    already approved — the one thing ratification exists to prevent.
    """
    units = await _proposed(service, features)
    await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)
    await service.amend(
        PID,
        PLAN,
        step_id="st1",
        partitions=(units[:2], units[2:]),
        rationale=_WHY,
        actor=OWNER,
        now=NOW,
    )
    with pytest.raises(NotRatifiedError):
        await service.begin_step(PID, PLAN, step_id="st1.1")


@pytest.mark.asyncio
async def test_re_ratifying_the_amended_version_unblocks_execution(
    service: ExecutionService, features: Features
) -> None:
    units = await _proposed(service, features)
    await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)
    await service.amend(
        PID, PLAN, step_id="st1", partitions=(units[:2], units[2:]),
        rationale=_WHY, actor=OWNER, now=NOW,
    )
    await service.ratify(PID, PLAN, plan_version=2, actor=OWNER, now=NOW)
    plan = await service.begin_step(PID, PLAN, step_id="st1.1")
    assert plan.step("st1.1").state is StepState.RUNNING


# ---------------------------------------------------------------------------
# Amendment — R-310-173
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_amendment_splits_a_step_and_advances_the_version(
    service: ExecutionService, features: Features
) -> None:
    units = await _proposed(service, features)
    plan = await service.amend(
        PID, PLAN, step_id="st1", partitions=(units[:2], units[2:]),
        rationale=_WHY, actor=OWNER, now=NOW,
    )
    assert plan.version == 2
    assert [step.step_id for step in plan.steps] == ["st1.1", "st1.2"]
    assert plan.amendments[-1].replaced_step_id == "st1"


@pytest.mark.asyncio
async def test_an_amendment_must_re_cover_exactly_the_scope_it_replaces(
    service: ExecutionService, features: Features
) -> None:
    """A lost unit drops accounted work; an added one smuggles in unpriced work."""
    units = await _proposed(service, features)
    with pytest.raises(PlanRefusedError, match="lost"):
        await service.amend(
            PID, PLAN, step_id="st1", partitions=(units[:1], units[1:2]),
            rationale=_WHY, actor=OWNER, now=NOW,
        )


@pytest.mark.asyncio
async def test_an_amendment_adding_an_unpriced_unit_is_refused(
    service: ExecutionService, features: Features
) -> None:
    units = await _proposed(service, features)
    with pytest.raises(PlanRefusedError, match="added"):
        await service.amend(
            PID, PLAN, step_id="st1",
            partitions=(units[:2], (*units[2:], "CT-999")),
            rationale=_WHY, actor=OWNER, now=NOW,
        )


@pytest.mark.asyncio
async def test_an_amendment_repeating_a_unit_is_refused(
    service: ExecutionService, features: Features
) -> None:
    units = await _proposed(service, features)
    with pytest.raises(PlanRefusedError, match="two partitions"):
        await service.amend(
            PID, PLAN, step_id="st1",
            partitions=((units[0], units[1]), (units[1], units[2], units[3])),
            rationale=_WHY, actor=OWNER, now=NOW,
        )


@pytest.mark.asyncio
async def test_a_completed_step_is_not_re_splittable(
    service: ExecutionService, features: Features
) -> None:
    """R-310-173: re-splitting must not invalidate work already done."""
    await _ratified(service, features)
    await service.complete_step(PID, PLAN, step_id="st1")
    plan = await service.get_plan(PID, PLAN)
    scope = plan.step("st1").scope
    with pytest.raises(PlanRefusedError, match="is completed"):
        await service.amend(
            PID, PLAN, step_id="st1", partitions=(scope[:2], scope[2:]),
            rationale=_WHY, actor=OWNER, now=NOW,
        )


@pytest.mark.asyncio
async def test_an_amendment_leaves_other_steps_untouched(
    service: ExecutionService, features: Features
) -> None:
    features.add("CT-001", impact_nodes=2, layers_crossed=1, containers=1)
    features.add("CT-002", impact_nodes=2, layers_crossed=1, containers=1)
    features.add("CT-050", impact_nodes=2, layers_crossed=5, containers=1)
    await service.propose(
        PID, PLAN, batch=BATCH, units=("CT-001", "CT-002", "CT-050"),
        actor=AGENT, now=NOW,
    )
    plan = await service.amend(
        PID, PLAN, step_id="st1", partitions=(("CT-001",), ("CT-002",)),
        rationale=_WHY, actor=OWNER, now=NOW,
    )
    assert "st2" in [step.step_id for step in plan.steps]
    assert plan.step("st2").scope == ("CT-050",)


@pytest.mark.asyncio
async def test_an_amendment_reclassifies_rather_than_carrying_the_bad_estimate(
    service: ExecutionService, features: Features
) -> None:
    """The reason to amend IS that the classification was wrong.

    Carrying it forward would preserve the estimate the amendment exists
    to fix.
    """
    features.add("CT-001", impact_nodes=2, layers_crossed=1, containers=1)
    features.add("CT-002", impact_nodes=2, layers_crossed=1, containers=1)
    await service.propose(
        PID, PLAN, batch=BATCH, units=("CT-001", "CT-002"), actor=AGENT, now=NOW
    )
    # The graph now knows CT-002 is far deeper than first measured.
    features.add("CT-002", impact_nodes=2, layers_crossed=6, containers=1)
    plan = await service.amend(
        PID, PLAN, step_id="st1", partitions=(("CT-001",), ("CT-002",)),
        rationale="Re-measured: CT-002 cascades through six layers.",
        actor=OWNER, now=NOW,
    )
    assert plan.step("st1.1").effort is EffortClass.BATCHABLE
    assert plan.step("st1.2").effort is EffortClass.REFLECTION_REQUIRED


# ---------------------------------------------------------------------------
# R-310-174 — suspension on overrun
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_step_within_its_estimate_keeps_running(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    await service.begin_step(PID, PLAN, step_id="st1")
    plan = await service.record_consumption(
        PID, PLAN, step_id="st1", consumed=Consumption(tokens=1, review_items=1)
    )
    assert plan.step("st1").state is StepState.RUNNING


@pytest.mark.asyncio
async def test_a_step_over_its_estimate_suspends_carrying_the_breach(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    await service.begin_step(PID, PLAN, step_id="st1")
    plan = await service.record_consumption(
        PID,
        PLAN,
        step_id="st1",
        consumed=Consumption(tokens=99_000_000, review_items=99_000),
    )
    step = plan.step("st1")
    assert step.state is StepState.SUSPENDED
    assert step.suspended_reason is not None
    assert "tokens" in step.suspended_reason
    assert "consumed 99,000,000" in step.suspended_reason


@pytest.mark.asyncio
async def test_a_suspended_step_is_not_a_failed_one(
    service: ExecutionService, features: Features
) -> None:
    """A step that went over did not go wrong; the remedies differ."""
    await _ratified(service, features)
    await service.begin_step(PID, PLAN, step_id="st1")
    plan = await service.record_consumption(
        PID, PLAN, step_id="st1", consumed=Consumption(tokens=99_000_000)
    )
    assert plan.step("st1").state is not StepState.FAILED
    assert plan.is_complete is False


@pytest.mark.asyncio
async def test_a_suspended_plan_is_findable_by_its_own_query(
    service: ExecutionService, features: Features
) -> None:
    """Otherwise it sits unnoticed among active plans — the R-310-174 failure."""
    await _ratified(service, features)
    await service.begin_step(PID, PLAN, step_id="st1")
    await service.record_consumption(
        PID, PLAN, step_id="st1", consumed=Consumption(tokens=99_000_000)
    )
    plan = await service.get_plan(PID, PLAN)
    assert plan.suspended_step_ids == ("st1",)


@pytest.mark.asyncio
async def test_a_terminal_step_does_not_change_state(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    await service.complete_step(PID, PLAN, step_id="st1")
    with pytest.raises(PlanRefusedError, match="terminal step"):
        await service.begin_step(PID, PLAN, step_id="st1")


@pytest.mark.asyncio
async def test_an_unknown_step_is_refused(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    with pytest.raises(PlanRefusedError, match="st9"):
        await service.begin_step(PID, PLAN, step_id="st9")


# ---------------------------------------------------------------------------
# R-310-175 — every version stays readable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_ratified_version_is_still_readable_after_an_amendment(
    service: ExecutionService, features: Features, storage: ExecutionStorage
) -> None:
    """A ratification is auditable only if its terms survive the amendment."""
    units = await _proposed(service, features)
    await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)
    await service.amend(
        PID, PLAN, step_id="st1", partitions=(units[:2], units[2:]),
        rationale=_WHY, actor=OWNER, now=NOW,
    )

    approved = await service.get_plan_version(PID, PLAN, 1)
    assert approved.version == 1
    assert approved.is_ratified is True
    assert [step.step_id for step in approved.steps] == ["st1"]
    assert await storage.list_versions(PID, PLAN) == (1, 2)


@pytest.mark.asyncio
async def test_asking_for_an_unstored_version_is_reported_missing(
    service: ExecutionService, features: Features
) -> None:
    await _proposed(service, features)
    with pytest.raises(PlanNotFoundError, match="version 9"):
        await service.get_plan_version(PID, PLAN, 9)


@pytest.mark.asyncio
async def test_plans_awaiting_ratification_include_amended_ones(
    service: ExecutionService, features: Features
) -> None:
    units = await _proposed(service, features)
    await service.ratify(PID, PLAN, plan_version=1, actor=OWNER, now=NOW)
    assert await service.list_plans(PID, awaiting_ratification=True) == ()

    await service.amend(
        PID, PLAN, step_id="st1", partitions=(units[:2], units[2:]),
        rationale=_WHY, actor=OWNER, now=NOW,
    )
    awaiting = await service.list_plans(PID, awaiting_ratification=True)
    assert [plan.plan_id for plan in awaiting] == [PLAN]


# ---------------------------------------------------------------------------
# R-310-176 — the treatment report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_report_is_refused_while_steps_are_outstanding(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    with pytest.raises(PlanRefusedError, match="outstanding"):
        await service.produce_report(PID, PLAN, now=NOW)


@pytest.mark.asyncio
async def test_a_report_names_what_was_processed(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    await service.complete_step(PID, PLAN, step_id="st1")
    report = await service.produce_report(
        PID,
        PLAN,
        containers_modified=("030-ARCH",),
        objects_created=("AD-100", "AD-101"),
        review_outcomes={"accepted": 2},
        coverage_before=10,
        coverage_after=14,
        now=NOW,
    )
    assert len(report.requirements_processed) == 4
    assert report.coverage_delta == 4
    assert report.is_clean is True


@pytest.mark.asyncio
async def test_a_failed_step_is_not_reported_as_processed(
    service: ExecutionService, features: Features
) -> None:
    """The report says what was done, not what was attempted."""
    await _ratified(service, features)
    await service.fail_step(PID, PLAN, step_id="st1")
    report = await service.produce_report(PID, PLAN, now=NOW)
    assert report.requirements_processed == ()


@pytest.mark.asyncio
async def test_a_report_is_readable_after_it_is_produced(
    service: ExecutionService, features: Features
) -> None:
    await _ratified(service, features)
    await service.complete_step(PID, PLAN, step_id="st1")
    await service.produce_report(PID, PLAN, now=NOW)
    stored = await service.get_report(PID, PLAN)
    assert stored is not None
    assert stored.plan_id == PLAN


@pytest.mark.asyncio
async def test_no_report_before_it_is_produced(
    service: ExecutionService, features: Features
) -> None:
    await _proposed(service, features)
    assert await service.get_report(PID, PLAN) is None
