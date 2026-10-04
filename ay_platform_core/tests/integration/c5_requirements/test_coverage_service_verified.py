# =============================================================================
# File: test_coverage_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_coverage_service_verified.py
# Description: Integration tests for CoverageService against real MinIO +
#              ArangoDB. The published cycle is real, so the scope-citation
#              rule is exercised against an actual process definition rather
#              than a stub.
#
#              What this tier establishes that no lower one can:
#                - an allocation citing a NEIGHBOUR's scope is refused just
#                  as firmly as one citing none (R-310-066) — the citation
#                  has to support the target, not merely exist;
#                - a container that returned a requirement is excluded from
#                  the next allocation, and the second return escalates
#                  (R-310-069);
#                - a requirement allocated to two containers and answered in
#                  one is partial, never covered (R-310-064);
#                - weak and stale coverage both leave an allocation
#                  UNCOVERED (R-310-122, R-310-145).
#
# @relation validates:R-310-064
# @relation validates:R-310-065
# @relation validates:R-310-066
# @relation validates:R-310-067
# @relation validates:R-310-068
# @relation validates:R-310-069
# @relation validates:R-310-120
# @relation validates:R-310-121
# @relation validates:R-310-122
# @relation validates:R-310-145
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.coverage.models import (
    CoverageStrength,
    ReturnReason,
)
from ay_platform_core.c5_requirements.coverage.repository import CoverageRepository
from ay_platform_core.c5_requirements.coverage.service import (
    AllocationRefusedError,
    CoverageRefusedError,
    CoverageService,
)
from ay_platform_core.c5_requirements.objects.models import ReviewState
from ay_platform_core.c5_requirements.process.models import ContainerSpec
from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.service import ProcessService
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
TENANT = "acme"
PID = "adas"
CYCLE = "C-AUTO"
ARCH = "030-ARCH"
SEC = "070-SEC"
REQ = "REQ-SYS-118"
ADMIN = "t.admin"
AGENT = "agent:allocator"
OWNER = "o.mathieu"

_JUST = "Torque allocation is declared by the architecture design container."

_SCOPE_AD = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)
_SCOPE_SEC = (
    "Threat analysis and risk assessment, attack paths, and the security "
    "controls allocated against them."
)


class Versions:
    """Injected current-version lookup, driven by the test."""

    def __init__(self) -> None:
        self.table: dict[str, int] = {REQ: 4}

    async def __call__(self, project_id: str, target_id: str) -> int | None:
        return self.table.get(target_id)


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[CoverageService, Versions, ProcessService]]:
    db_name = f"c5_covsvc_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        process_repo = ProcessRepository(db)
        process_repo._ensure_collections_sync()
        process = ProcessService(ProcessStorage(c5_storage), process_repo)
        coverage_repo = CoverageRepository(db)
        coverage_repo._ensure_collections_sync()
        versions = Versions()
        yield CoverageService(coverage_repo, process, versions), versions, process
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
def service(
    wired: tuple[CoverageService, Versions, ProcessService],
) -> CoverageService:
    return wired[0]


@pytest.fixture
def versions(wired: tuple[CoverageService, Versions, ProcessService]) -> Versions:
    return wired[1]


@pytest.fixture
async def published_cycle(
    wired: tuple[CoverageService, Versions, ProcessService],
) -> None:
    process = wired[2]
    await process.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=(
            ContainerSpec(
                slug=ARCH, ordinal=30, scope_id="SC-002", scope=_SCOPE_AD
            ),
            ContainerSpec(
                slug=SEC, ordinal=70, scope_id="SC-003", scope=_SCOPE_SEC
            ),
        ),
        actor=ADMIN,
    )
    await process.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)


async def _allocate(service: CoverageService, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "requirement_id": REQ, "container": ARCH, "scope_id": "SC-002",
        "justification": _JUST, "actor": AGENT,
    }
    kwargs.update(overrides)
    return await service.allocate(TENANT, PID, CYCLE, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The scope citation — R-310-066
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_citation_is_accepted(
    service: CoverageService, published_cycle: None
) -> None:
    allocation = await _allocate(service)
    assert allocation.scope_id == "SC-002"  # type: ignore[attr-defined]
    assert allocation.state is ReviewState.PROPOSED  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_citing_a_neighbours_scope_is_refused(
    service: CoverageService, published_cycle: None
) -> None:
    """The citation must SUPPORT the target, not merely exist.

    This is the case a format check alone would let through, and it is the
    commonest way an agent allocating by lexical similarity goes wrong.
    """
    with pytest.raises(AllocationRefusedError, match="is not the scope of"):
        await _allocate(service, scope_id="SC-003")


@pytest.mark.asyncio
async def test_citing_an_unknown_scope_is_refused(
    service: CoverageService, published_cycle: None
) -> None:
    with pytest.raises(AllocationRefusedError, match="is not the scope of"):
        await _allocate(service, scope_id="SC-999")


@pytest.mark.asyncio
async def test_an_unknown_container_is_refused(
    service: CoverageService, published_cycle: None
) -> None:
    with pytest.raises(AllocationRefusedError, match="declares no container"):
        await _allocate(service, container="999-NOPE")


@pytest.mark.asyncio
async def test_no_published_cycle_refuses_allocation(
    service: CoverageService,
) -> None:
    with pytest.raises(AllocationRefusedError, match="no published cycle"):
        await _allocate(service)


# ---------------------------------------------------------------------------
# Criticality — R-310-067
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_criticality_propagates_by_default(
    service: CoverageService, published_cycle: None
) -> None:
    allocation = await _allocate(service, inherited_criticality="ASIL-D")
    assert allocation.criticality == "ASIL-D"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lowering_criticality_without_a_decomposition_is_refused(
    service: CoverageService, published_cycle: None
) -> None:
    with pytest.raises(AllocationRefusedError, match="without a recorded decomposition"):
        await _allocate(service, inherited_criticality="ASIL-D", criticality="ASIL-B")


@pytest.mark.asyncio
async def test_a_recorded_decomposition_is_permitted(
    service: CoverageService, published_cycle: None
) -> None:
    """A tool that refused every decomposition would contradict the standard."""
    allocation = await _allocate(
        service,
        inherited_criticality="ASIL-D",
        criticality="ASIL-B",
        decomposition_rationale="ASIL-D(D) decomposed to ASIL-B(D) + ASIL-B(D).",
    )
    assert allocation.criticality == "ASIL-B"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Returns — R-310-068 / R-310-069
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_return_drops_the_allocation_and_excludes_the_container(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service)
    outcome = await service.return_allocation(
        PID, REQ, ARCH, reason=ReturnReason.OUT_OF_SCOPE,
        detail="Torque limits are technical design, not architecture.",
        actor=OWNER,
    )
    assert outcome.ordinal == 1
    assert outcome.escalated is False
    assert outcome.next_exclusions == (ARCH,)

    with pytest.raises(AllocationRefusedError, match="already returned"):
        await _allocate(service)


@pytest.mark.asyncio
async def test_a_human_may_deliberately_override_the_exclusion(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service)
    await service.return_allocation(
        PID, REQ, ARCH, reason=ReturnReason.OUT_OF_SCOPE,
        detail="Torque limits are technical design, not architecture.",
        actor=OWNER,
    )
    allocation = await _allocate(service, override_exclusion=True)
    assert allocation.container == ARCH  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_second_return_escalates(
    service: CoverageService, published_cycle: None
) -> None:
    """R-310-069 — without a terminator, two owners loop indefinitely."""
    for _ in range(2):
        await _allocate(service, override_exclusion=True)
        outcome = await service.return_allocation(
            PID, REQ, ARCH, reason=ReturnReason.OUT_OF_SCOPE,
            detail="Torque limits are technical design, not architecture.",
            actor=OWNER,
        )
    assert outcome.ordinal == 2
    assert outcome.escalated is True


@pytest.mark.asyncio
async def test_the_return_history_survives_the_dropped_allocation(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service)
    await service.return_allocation(
        PID, REQ, ARCH, reason=ReturnReason.NOT_ATOMIC,
        detail="Only the timing half belongs here; the ramp is technical.",
        actor=OWNER,
    )
    coverage = await service.requirement_coverage(PID, REQ)
    assert coverage.allocations == ()
    # But the exclusion is still known, which is the point.
    outcome = await service.return_allocation(
        PID, REQ, ARCH, reason=ReturnReason.NOT_ATOMIC,
        detail="Only the timing half belongs here; the ramp is technical.",
        actor=OWNER,
    )
    assert outcome.ordinal == 2


# ---------------------------------------------------------------------------
# The unallocated audit — R-310-065
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_out_of_project_verdict_settles_a_requirement(
    service: CoverageService, published_cycle: None
) -> None:
    await service.record_out_of_project(
        PID, "REQ-9",
        justification="Homologation is the OEM's responsibility, not ours.",
        actor=OWNER,
    )
    assert await service.audit_unallocated(PID, ["REQ-9"]) == []
    assert (await service.requirement_coverage(PID, "REQ-9")).is_covered is True


@pytest.mark.asyncio
async def test_an_undecided_requirement_is_surfaced(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service)
    assert await service.audit_unallocated(PID, [REQ, "REQ-9"]) == ["REQ-9"]


# ---------------------------------------------------------------------------
# Coverage links — R-310-121 / R-310-122 / R-310-145
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_covering_an_unallocated_target_is_refused(
    service: CoverageService, published_cycle: None
) -> None:
    """R-310-121 — an object cannot answer something nobody asked it."""
    with pytest.raises(CoverageRefusedError, match="not allocated to"):
        await service.cover(
            PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
        )


@pytest.mark.asyncio
async def test_coverage_pins_the_current_target_version(
    service: CoverageService, published_cycle: None, versions: Versions
) -> None:
    await _allocate(service)
    link = await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
    )
    assert link.pinned_version == 4


@pytest.mark.asyncio
async def test_covering_a_versionless_target_is_refused(
    service: CoverageService, published_cycle: None, versions: Versions
) -> None:
    # A pin against nothing could never go stale, so the link would be a
    # permanent false green.
    await _allocate(service, requirement_id="REQ-UNKNOWN")
    with pytest.raises(CoverageRefusedError, match="no current version to pin"):
        await service.cover(
            PID, object_id="OBJ-1", container=ARCH, target_id="REQ-UNKNOWN",
            actor=AGENT,
        )


@pytest.mark.asyncio
async def test_a_covered_requirement_reports_covered(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service)
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
    )
    coverage = await service.requirement_coverage(PID, REQ)
    assert coverage.is_covered is True
    assert coverage.allocations[0].covering_objects == ("OBJ-1",)


@pytest.mark.asyncio
async def test_weak_coverage_leaves_it_uncovered(
    service: CoverageService, published_cycle: None
) -> None:
    """R-310-122 — citing without answering is not answering."""
    await _allocate(service)
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT,
        strength=CoverageStrength.WEAK,
    )
    coverage = await service.requirement_coverage(PID, REQ)
    assert coverage.is_covered is False
    assert coverage.allocations[0].weak_objects == ("OBJ-1",)
    assert coverage.allocations[0].covering_objects == ()


@pytest.mark.asyncio
async def test_a_stale_link_leaves_it_uncovered(
    service: CoverageService, published_cycle: None, versions: Versions
) -> None:
    """R-310-145 — an object answering a version that has moved is not an
    answer to the current one."""
    await _allocate(service)
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
    )
    versions.table[REQ] = 5  # the customer changed the requirement

    coverage = await service.requirement_coverage(PID, REQ)
    assert coverage.is_covered is False
    assert coverage.allocations[0].stale_objects == ("OBJ-1",)


@pytest.mark.asyncio
async def test_suspect_links_are_computed_not_stored(
    service: CoverageService, published_cycle: None, versions: Versions
) -> None:
    await _allocate(service)
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
    )
    assert await service.suspect_links(PID) == []

    versions.table[REQ] = 5
    suspect = await service.suspect_links(PID)
    assert len(suspect) == 1
    assert suspect[0]["pinned_version"] == 4
    assert suspect[0]["current_version"] == 5


# ---------------------------------------------------------------------------
# Aggregation — R-310-064
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_allocations_answered_in_one_is_partial(
    service: CoverageService, published_cycle: None
) -> None:
    """R-310-064 — the assertion the whole model exists to make."""
    await _allocate(service, container=ARCH, scope_id="SC-002")
    await _allocate(service, container=SEC, scope_id="SC-003")
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
    )

    coverage = await service.requirement_coverage(PID, REQ)
    assert coverage.is_covered is False
    assert coverage.is_partial is True


@pytest.mark.asyncio
async def test_both_allocations_answered_is_covered(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service, container=ARCH, scope_id="SC-002")
    await _allocate(service, container=SEC, scope_id="SC-003")
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id=REQ, actor=AGENT
    )
    await service.cover(
        PID, object_id="SEC-1", container=SEC, target_id=REQ, actor=AGENT
    )
    assert (await service.requirement_coverage(PID, REQ)).is_covered is True


@pytest.mark.asyncio
async def test_cluster_acceptance_is_recorded_distinctly(
    service: CoverageService, published_cycle: None
) -> None:
    # R-310-007: a coverage figure must be able to say how much was never
    # examined individually.
    await _allocate(service)
    accepted = await service.accept_allocation(PID, REQ, ARCH, actor="cluster:114", auto=True)
    assert accepted.state is ReviewState.AUTO_ACCEPTED

    coverage = await service.requirement_coverage(PID, REQ)
    assert coverage.allocations[0].allocation_state is ReviewState.AUTO_ACCEPTED


@pytest.mark.asyncio
async def test_accepting_a_missing_allocation_is_refused(
    service: CoverageService, published_cycle: None
) -> None:
    with pytest.raises(AllocationRefusedError, match="to accept"):
        await service.accept_allocation(PID, REQ, ARCH, actor=OWNER)


# ---------------------------------------------------------------------------
# Container view — R-310-120
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_container_is_incomplete_while_something_is_owed(
    service: CoverageService, published_cycle: None
) -> None:
    await _allocate(service, requirement_id="REQ-1")
    await _allocate(service, requirement_id="REQ-2")
    assert (await service.container_coverage(PID, ARCH)).is_complete is False


@pytest.mark.asyncio
async def test_a_container_is_complete_when_everything_is_answered(
    service: CoverageService, published_cycle: None, versions: Versions
) -> None:
    versions.table.update({"REQ-1": 1, "REQ-2": 1})
    await _allocate(service, requirement_id="REQ-1")
    await _allocate(service, requirement_id="REQ-2")
    await service.cover(
        PID, object_id="OBJ-1", container=ARCH, target_id="REQ-1", actor=AGENT
    )
    await service.cover(
        PID, object_id="OBJ-2", container=ARCH, target_id="REQ-2", actor=AGENT
    )
    container = await service.container_coverage(PID, ARCH)
    assert container.is_complete is True
    assert container.allocated == ("REQ-1", "REQ-2")


@pytest.mark.asyncio
async def test_the_container_view_separates_the_three_problems(
    service: CoverageService, published_cycle: None, versions: Versions
) -> None:
    """The workbench asks "what do I fix first?"; collapsing them makes that
    unanswerable."""
    versions.table.update({"REQ-1": 1, "REQ-2": 1, "REQ-3": 1})
    for requirement in ("REQ-1", "REQ-2", "REQ-3"):
        await _allocate(service, requirement_id=requirement)
    await service.cover(
        PID, object_id="OBJ-2", container=ARCH, target_id="REQ-2", actor=AGENT,
        strength=CoverageStrength.WEAK,
    )
    await service.cover(
        PID, object_id="OBJ-3", container=ARCH, target_id="REQ-3", actor=AGENT
    )
    versions.table["REQ-3"] = 2

    container = await service.container_coverage(PID, ARCH)
    assert container.uncovered == ("REQ-1", "REQ-2", "REQ-3")
    assert container.weak == ("REQ-2",)
    assert container.stale == ("REQ-3",)
