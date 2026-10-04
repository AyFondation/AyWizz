# =============================================================================
# File: test_coverage_repository_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_coverage_repository_verified.py
# Description: Integration tests for CoverageRepository against a real
#              ArangoDB. Everything here is AQL, so none of it means anything
#              unexecuted.
#
#              Three properties this tier establishes:
#                - returns ACCUMULATE rather than replace (R-310-069
#                  escalates on the second, which is only countable if the
#                  first survives);
#                - dropping an allocation leaves its return history intact —
#                  the returns are what the next allocation reads
#                  (R-310-068);
#                - the unallocated audit asks the question of a CANDIDATE
#                  set, not of what happens to be stored, because the failure
#                  R-310-065 closes is silence.
#
# @relation validates:R-310-064
# @relation validates:R-310-065
# @relation validates:R-310-068
# @relation validates:R-310-069
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.coverage.models import (
    Allocation,
    AllocationRejection,
    CoverageLink,
    CoverageStrength,
    OutOfProjectVerdict,
    ReturnReason,
)
from ay_platform_core.c5_requirements.coverage.repository import (
    COLL_ALLOCATIONS,
    COLL_COVERAGE,
    CoverageRepository,
)
from ay_platform_core.c5_requirements.objects.models import ReviewState
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
PID = "adas"
REQ = "REQ-SYS-118"
ARCH = "030-ARCH"
TECH = "050-TECH"

_JUST = "Torque allocation is declared by the architecture design container."
_DETAIL = "Only the timing half belongs here; the ramp is technical design."


@pytest.fixture
def repo(arango_container: ArangoEndpoint) -> Iterator[CoverageRepository]:
    db_name = f"c5_cov_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        r = CoverageRepository(db)
        r._ensure_collections_sync()
        yield r
    finally:
        cleanup_arango_database(arango_container, db_name)


def _allocation(**overrides: Any) -> Allocation:
    payload: dict[str, Any] = {
        "requirement_id": REQ, "project_id": PID, "container": ARCH,
        "scope_id": "SC-002", "justification": _JUST,
        "actor": "agent:allocator", "at": NOW,
    }
    payload.update(overrides)
    return Allocation.model_validate(payload)


def _rejection(**overrides: Any) -> AllocationRejection:
    payload: dict[str, Any] = {
        "requirement_id": REQ, "project_id": PID, "container": ARCH,
        "reason": ReturnReason.OUT_OF_SCOPE, "detail": _DETAIL,
        "actor": "o.mathieu", "at": NOW,
    }
    payload.update(overrides)
    return AllocationRejection.model_validate(payload)


def _link(**overrides: Any) -> CoverageLink:
    payload: dict[str, Any] = {
        "object_id": "OBJ-1120", "project_id": PID, "container": ARCH,
        "target_id": REQ, "pinned_version": 4,
        "actor": "agent:architect", "at": NOW,
    }
    payload.update(overrides)
    return CoverageLink.model_validate(payload)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(repo: CoverageRepository) -> None:
    await repo.ensure_collections()
    await repo.ensure_collections()
    names = {c["name"] for c in repo._db.collections()}
    assert COLL_ALLOCATIONS in names
    assert COLL_COVERAGE in names


@pytest.mark.asyncio
async def test_coverage_is_an_edge_collection(repo: CoverageRepository) -> None:
    """The impact DAG of §4.8 has to traverse it, so it must be a real edge."""
    properties = repo._db.collection(COLL_COVERAGE).properties()
    assert properties["edge"] is True


@pytest.mark.asyncio
async def test_allocations_is_a_document_collection(
    repo: CoverageRepository,
) -> None:
    """A container is not a document, so this cannot be an edge.

    E-310-006 calls it an edge; that entity needs correcting. Creating
    container vertices to satisfy the shape would duplicate cycle data in a
    second place able to drift from it.
    """
    properties = repo._db.collection(COLL_ALLOCATIONS).properties()
    assert properties["edge"] is False


# ---------------------------------------------------------------------------
# Allocation decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allocation_round_trip(repo: CoverageRepository) -> None:
    await repo.put_allocation(_allocation())
    rows = await repo.decisions(PID, REQ)
    assert len(rows) == 1
    assert rows[0]["kind"] == "allocation"
    assert rows[0]["scope_id"] == "SC-002"
    assert rows[0]["state"] == "proposed"


@pytest.mark.asyncio
async def test_one_requirement_holds_several_allocations(
    repo: CoverageRepository,
) -> None:
    """R-310-064 — multi-container allocation is the canonical model."""
    await repo.put_allocation(_allocation(container=ARCH))
    await repo.put_allocation(_allocation(container=TECH, scope_id="SC-003"))
    rows = await repo.decisions(PID, REQ)
    assert sorted(r["container"] for r in rows) == [ARCH, TECH]


@pytest.mark.asyncio
async def test_re_allocating_the_same_pair_replaces_it(
    repo: CoverageRepository,
) -> None:
    await repo.put_allocation(_allocation())
    await repo.put_allocation(_allocation(state=ReviewState.ACCEPTED))
    rows = await repo.decisions(PID, REQ)
    assert len(rows) == 1
    assert rows[0]["state"] == "accepted"


@pytest.mark.asyncio
async def test_verdict_is_stored_alongside_allocations(
    repo: CoverageRepository,
) -> None:
    # One collection answers "what was decided about where this is answered";
    # splitting it would make the R-310-065 audit a join.
    await repo.put_verdict(
        OutOfProjectVerdict(
            requirement_id=REQ, project_id=PID,
            justification="Homologation is the OEM's responsibility, not ours.",
            actor="o.mathieu", at=NOW,
        )
    )
    rows = await repo.decisions(PID, REQ)
    assert [r["kind"] for r in rows] == ["out-of-project"]


@pytest.mark.asyncio
async def test_projects_are_isolated(repo: CoverageRepository) -> None:
    await repo.put_allocation(_allocation(project_id="p1"))
    await repo.put_allocation(_allocation(project_id="p2"))
    assert len(await repo.decisions("p1", REQ)) == 1


# ---------------------------------------------------------------------------
# Returns — R-310-068 / R-310-069
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_accumulate_and_are_counted(
    repo: CoverageRepository,
) -> None:
    """R-310-069 escalates on the SECOND return, so the first must survive."""
    assert await repo.put_return(_rejection()) == 1
    assert await repo.put_return(_rejection(reason=ReturnReason.NOT_ATOMIC)) == 2
    assert await repo.count_returns(PID, REQ, ARCH) == 2


@pytest.mark.asyncio
async def test_returns_are_counted_per_container(
    repo: CoverageRepository,
) -> None:
    await repo.put_return(_rejection(container=ARCH))
    await repo.put_return(_rejection(container=TECH))
    assert await repo.count_returns(PID, REQ, ARCH) == 1
    assert await repo.count_returns(PID, REQ, TECH) == 1


@pytest.mark.asyncio
async def test_returned_containers_are_listed_for_exclusion(
    repo: CoverageRepository,
) -> None:
    # R-310-069: proposing a target that already said no is how the loop
    # starts, so re-allocation needs this list.
    await repo.put_return(_rejection(container=ARCH))
    await repo.put_return(_rejection(container=TECH))
    assert await repo.returned_containers(PID, REQ) == [ARCH, TECH]


@pytest.mark.asyncio
async def test_dropping_an_allocation_keeps_its_return_history(
    repo: CoverageRepository,
) -> None:
    """R-310-068 — the returns are what the next allocation reads."""
    await repo.put_allocation(_allocation())
    await repo.put_return(_rejection())

    assert await repo.drop_allocation(PID, REQ, ARCH) is True

    assert await repo.count_returns(PID, REQ, ARCH) == 1
    kinds = [r["kind"] for r in await repo.decisions(PID, REQ)]
    assert kinds == ["return"]


@pytest.mark.asyncio
async def test_dropping_an_absent_allocation_reports_false(
    repo: CoverageRepository,
) -> None:
    assert await repo.drop_allocation(PID, REQ, ARCH) is False


@pytest.mark.asyncio
async def test_a_return_carries_its_routed_reason(
    repo: CoverageRepository,
) -> None:
    # Each code routes differently: re-allocation, splitting, or back to the
    # issuer. Storing it as a code is what makes routing possible.
    await repo.put_return(_rejection(reason=ReturnReason.NOT_ATOMIC))
    rows = await repo.decisions(PID, REQ)
    assert rows[0]["reason"] == "not-atomic"
    assert rows[0]["detail"] == _DETAIL


# ---------------------------------------------------------------------------
# The unallocated audit — R-310-065
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unallocated_asks_the_candidate_set(
    repo: CoverageRepository,
) -> None:
    """The failure R-310-065 closes is SILENCE, so the question is asked of
    the requirements that exist, not of the rows that happen to be stored."""
    await repo.put_allocation(_allocation(requirement_id="REQ-1"))
    await repo.put_verdict(
        OutOfProjectVerdict(
            requirement_id="REQ-2", project_id=PID,
            justification="Homologation is the OEM's responsibility, not ours.",
            actor="o.mathieu", at=NOW,
        )
    )
    missing = await repo.unallocated(PID, ["REQ-1", "REQ-2", "REQ-3", "REQ-4"])
    assert missing == ["REQ-3", "REQ-4"]


@pytest.mark.asyncio
async def test_a_return_alone_does_not_count_as_decided(
    repo: CoverageRepository,
) -> None:
    # A returned requirement is back in limbo: it needs a new allocation or
    # a verdict, and the audit must keep surfacing it until it gets one.
    await repo.put_return(_rejection(requirement_id="REQ-1"))
    assert await repo.unallocated(PID, ["REQ-1"]) == ["REQ-1"]


@pytest.mark.asyncio
async def test_unallocated_with_no_candidates_is_empty(
    repo: CoverageRepository,
) -> None:
    assert await repo.unallocated(PID, []) == []


# ---------------------------------------------------------------------------
# Coverage links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_link_round_trip(repo: CoverageRepository) -> None:
    await repo.put_coverage(_link())
    rows = await repo.coverage_links(PID, REQ)
    assert len(rows) == 1
    assert rows[0]["pinned_version"] == 4
    assert rows[0]["strength"] == "covered"


@pytest.mark.asyncio
async def test_coverage_edge_carries_traversable_handles(
    repo: CoverageRepository,
) -> None:
    """`_from` / `_to` for the impact DAG; flat ids for the matrix."""
    await repo.put_coverage(_link())
    row = (await repo.coverage_links(PID, REQ))[0]
    assert row["_from"] == "req_objects/adas:OBJ-1120"
    assert row["_to"] == "req_entities/adas:REQ-SYS-118"
    assert row["object_id"] == "OBJ-1120"
    assert row["target_id"] == REQ


@pytest.mark.asyncio
async def test_several_objects_may_cover_one_requirement(
    repo: CoverageRepository,
) -> None:
    await repo.put_coverage(_link(object_id="OBJ-1"))
    await repo.put_coverage(_link(object_id="OBJ-2"))
    rows = await repo.coverage_links(PID, REQ)
    assert sorted(r["object_id"] for r in rows) == ["OBJ-1", "OBJ-2"]


@pytest.mark.asyncio
async def test_coverage_filtered_by_container(repo: CoverageRepository) -> None:
    await repo.put_coverage(_link(object_id="OBJ-1", container=ARCH))
    await repo.put_coverage(_link(object_id="OBJ-2", container=TECH))
    rows = await repo.coverage_links(PID, container=TECH)
    assert [r["object_id"] for r in rows] == ["OBJ-2"]


@pytest.mark.asyncio
async def test_weak_coverage_is_stored_distinctly(
    repo: CoverageRepository,
) -> None:
    # R-310-122: folding weak into covered is what turns a matrix green and
    # untrustworthy, so it has to survive the round trip.
    await repo.put_coverage(_link(strength=CoverageStrength.WEAK))
    assert (await repo.coverage_links(PID, REQ))[0]["strength"] == "weak"


@pytest.mark.asyncio
async def test_dropping_a_coverage_link(repo: CoverageRepository) -> None:
    await repo.put_coverage(_link())
    assert await repo.drop_coverage(PID, "OBJ-1120", REQ) is True
    assert await repo.drop_coverage(PID, "OBJ-1120", REQ) is False
    assert await repo.coverage_links(PID, REQ) == []


@pytest.mark.asyncio
async def test_coverage_by_target_groups_the_whole_matrix(
    repo: CoverageRepository,
) -> None:
    """One query for the matrix: per-requirement would be thousands of round
    trips at the volume of R-310-300."""
    await repo.put_coverage(_link(object_id="OBJ-1", target_id="REQ-1", container=ARCH))
    await repo.put_coverage(_link(object_id="OBJ-2", target_id="REQ-1", container=ARCH))
    await repo.put_coverage(_link(object_id="OBJ-3", target_id="REQ-1", container=TECH))
    await repo.put_coverage(_link(object_id="OBJ-4", target_id="REQ-2", container=ARCH))

    grouped = {
        (g["target_id"], g["container"]): g["links"]
        for g in await repo.coverage_by_target(PID)
    }
    assert sorted(o["object_id"] for o in grouped[("REQ-1", ARCH)]) == [
        "OBJ-1", "OBJ-2",
    ]
    assert [o["object_id"] for o in grouped[("REQ-1", TECH)]] == ["OBJ-3"]
    assert [o["object_id"] for o in grouped[("REQ-2", ARCH)]] == ["OBJ-4"]


@pytest.mark.asyncio
async def test_coverage_grouping_carries_what_the_matrix_needs(
    repo: CoverageRepository,
) -> None:
    await repo.put_coverage(
        _link(strength=CoverageStrength.WEAK, state=ReviewState.ACCEPTED)
    )
    links = (await repo.coverage_by_target(PID))[0]["links"]
    assert links[0]["strength"] == "weak"
    assert links[0]["state"] == "accepted"
    assert links[0]["pinned_version"] == 4
