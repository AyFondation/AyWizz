# =============================================================================
# File: test_coverage_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_coverage_models.py
# Description: Unit tests for the traceability graph contracts of
#              310-SPEC §4.4 / §4.5 / §4.7.
#
#              The load-bearing assertions:
#                - a requirement allocated to three containers and answered
#                  in two is NOT covered (R-310-064) — partial is a distinct,
#                  nameable state;
#                - a split requirement is covered through its fragments and
#                  never through its own allocations (R-310-096);
#                - weak coverage never counts as coverage (R-310-122);
#                - staleness is computed against the target's current
#                  version, never stored (R-310-145).
#
# @relation validates:R-310-064
# @relation validates:R-310-065
# @relation validates:R-310-066
# @relation validates:R-310-068
# @relation validates:R-310-096
# @relation validates:R-310-122
# @relation validates:R-310-145
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.coverage.models import (
    Allocation,
    AllocationCoverage,
    AllocationRejection,
    ContainerCoverage,
    CoverageLink,
    CoverageStrength,
    OutOfProjectVerdict,
    RequirementCoverage,
    ReturnReason,
    is_fragment,
    is_valid_requirement_id,
    parent_requirement,
)
from ay_platform_core.c5_requirements.objects.models import ReviewState

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
PID = "adas"
REQ = "REQ-SYS-118"
ARCH = "030-ARCH"

_JUST = "Torque allocation is declared by the architecture design container."


def _allocation(**overrides: Any) -> Allocation:
    payload: dict[str, Any] = {
        "requirement_id": REQ,
        "project_id": PID,
        "container": ARCH,
        "scope_id": "SC-002",
        "justification": _JUST,
        "actor": "agent:allocator",
        "at": NOW,
    }
    payload.update(overrides)
    return Allocation.model_validate(payload)


def _covered(container: str = ARCH, *, objects: tuple[str, ...] = ("OBJ-1",)) -> AllocationCoverage:
    return AllocationCoverage(
        container=container,
        allocation_state=ReviewState.ACCEPTED,
        covering_objects=objects,
    )


def _uncovered(container: str) -> AllocationCoverage:
    return AllocationCoverage(container=container, allocation_state=ReviewState.ACCEPTED)


@pytest.mark.unit
class TestRequirementIdentity:
    def test_supplied_and_fragment_ids(self) -> None:
        for good in ("REQ-SYS-118", "REQ-SYS-118/2", "REQ-SEC-034", "T-SYS-118-01"):
            assert is_valid_requirement_id(good), good

    def test_malformed_ids_rejected(self) -> None:
        for bad in ("req-sys-118", "REQ", "REQ-", "REQ-SYS-118/", "/2", ""):
            assert not is_valid_requirement_id(bad), bad

    def test_fragment_detection(self) -> None:
        assert is_fragment("REQ-SYS-118/2")
        assert not is_fragment("REQ-SYS-118")

    def test_parent_of_a_fragment(self) -> None:
        # R-310-095: the namespace is visibly distinct, and the parent is
        # recoverable without a lookup.
        assert parent_requirement("REQ-SYS-118/2") == "REQ-SYS-118"
        assert parent_requirement("REQ-SYS-118") == "REQ-SYS-118"


@pytest.mark.unit
class TestAllocation:
    def test_valid_allocation(self) -> None:
        assert _allocation().state is ReviewState.PROPOSED

    def test_scope_citation_is_mandatory_and_typed(self) -> None:
        # R-310-066: an allocation that cites no scope cannot be reviewed.
        with pytest.raises(ValidationError, match="SHALL cite the container scope"):
            _allocation(scope_id="SCOPE-1")

    def test_justification_must_be_substantive(self) -> None:
        with pytest.raises(ValidationError, match="SHALL justify itself"):
            _allocation(justification="because")

    def test_malformed_requirement_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid requirement id"):
            _allocation(requirement_id="req-118")

    def test_container_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid container slug"):
            _allocation(container="../escape")

    def test_decomposition_rationale_is_carried(self) -> None:
        # R-310-067: criticality propagates unless a decomposition is
        # explicitly recorded. The model carries the record; the service
        # enforces the rule.
        allocation = _allocation(
            criticality="ASIL-D",
            decomposition_rationale="ASIL-D(D) decomposed to ASIL-B(D) + ASIL-B(D).",
        )
        assert allocation.decomposition_rationale is not None


@pytest.mark.unit
class TestVerdictsAndReturns:
    def test_out_of_project_needs_a_justification(self) -> None:
        # R-310-065: the failure this closes is SILENCE — an agent omitting
        # what it cannot place. A verdict is reviewable; an omission is not.
        with pytest.raises(ValidationError, match="SHALL justify itself"):
            OutOfProjectVerdict(
                requirement_id=REQ, project_id=PID, justification="no",
                actor="agent:allocator", at=NOW,
            )

    def test_return_reasons_are_the_three_routed_codes(self) -> None:
        # Each routes to a different outcome: re-allocation, splitting, or
        # back to the issuer. A free-text reason could not be routed on.
        assert {r.value for r in ReturnReason} == {
            "out-of-scope", "not-atomic", "out-of-project",
        }

    def test_return_needs_a_detail(self) -> None:
        with pytest.raises(ValidationError, match="SHALL say why"):
            AllocationRejection(
                requirement_id=REQ, project_id=PID, container=ARCH,
                reason=ReturnReason.OUT_OF_SCOPE, detail="nope",
                actor="o.mathieu", at=NOW,
            )

    def test_valid_return(self) -> None:
        rejection = AllocationRejection(
            requirement_id=REQ, project_id=PID, container=ARCH,
            reason=ReturnReason.NOT_ATOMIC,
            detail="Only the timing half belongs here; the ramp is technical.",
            actor="o.mathieu", at=NOW,
        )
        assert rejection.reason is ReturnReason.NOT_ATOMIC


@pytest.mark.unit
class TestCoverageLink:
    def _link(self, **overrides: Any) -> CoverageLink:
        payload: dict[str, Any] = {
            "object_id": "OBJ-1120",
            "project_id": PID,
            "container": ARCH,
            "target_id": REQ,
            "pinned_version": 4,
            "actor": "agent:architect",
            "at": NOW,
        }
        payload.update(overrides)
        return CoverageLink.model_validate(payload)

    def test_staleness_is_computed_not_stored(self) -> None:
        """R-310-145 — a stored flag drifts the moment the target moves."""
        link = self._link(pinned_version=4)
        assert not link.is_stale(4)
        assert link.is_stale(5)
        assert "stale" not in CoverageLink.model_fields

    def test_a_pin_is_mandatory(self) -> None:
        with pytest.raises(ValidationError):
            self._link(pinned_version=0)

    def test_weak_is_a_distinct_strength(self) -> None:
        # R-310-122: folding weak into covered is what turns a matrix green
        # and untrustworthy.
        assert self._link(strength=CoverageStrength.WEAK).strength is (
            CoverageStrength.WEAK
        )
        assert {s.value for s in CoverageStrength} == {"covered", "weak"}


@pytest.mark.unit
class TestAggregatedCoverage:
    def test_every_allocation_must_be_covered(self) -> None:
        """R-310-064 — two out of three is not covered, it is partial."""
        coverage = RequirementCoverage(
            requirement_id=REQ,
            allocations=(_covered("010-FD"), _covered("030-AD"), _uncovered("070-SEC")),
        )
        assert coverage.is_covered is False
        assert coverage.is_partial is True

    def test_all_allocations_covered_is_covered(self) -> None:
        coverage = RequirementCoverage(
            requirement_id=REQ, allocations=(_covered("010-FD"), _covered("030-AD")),
        )
        assert coverage.is_covered is True
        assert coverage.is_partial is False

    def test_nothing_covered_is_neither_covered_nor_partial(self) -> None:
        coverage = RequirementCoverage(
            requirement_id=REQ, allocations=(_uncovered("010-FD"),)
        )
        assert coverage.is_covered is False
        assert coverage.is_partial is False

    def test_weak_objects_do_not_count_as_coverage(self) -> None:
        # An object citing the requirement without answering it leaves the
        # allocation uncovered (R-310-122).
        allocation = AllocationCoverage(
            container=ARCH, allocation_state=ReviewState.ACCEPTED,
            weak_objects=("OBJ-1132",),
        )
        assert allocation.is_covered is False
        assert RequirementCoverage(
            requirement_id=REQ, allocations=(allocation,)
        ).is_covered is False

    def test_an_unallocated_requirement_is_flagged(self) -> None:
        # R-310-065: nothing decided at all is the state the audit looks for.
        coverage = RequirementCoverage(requirement_id=REQ)
        assert coverage.is_unallocated is True
        assert coverage.is_covered is False

    def test_out_of_project_counts_as_settled(self) -> None:
        coverage = RequirementCoverage(requirement_id=REQ, out_of_project=True)
        assert coverage.is_unallocated is False
        assert coverage.is_covered is True

    def test_a_split_requirement_is_covered_through_its_fragments(self) -> None:
        """R-310-096 — the aggregate, never a directly settable answer."""
        coverage = RequirementCoverage(
            requirement_id=REQ,
            fragments=(
                RequirementCoverage(
                    requirement_id="REQ-SYS-118/1", allocations=(_covered("010-FD"),)
                ),
                RequirementCoverage(
                    requirement_id="REQ-SYS-118/2", allocations=(_uncovered("030-AD"),)
                ),
            ),
        )
        assert coverage.is_covered is False
        assert coverage.is_partial is True

    def test_all_fragments_covered_covers_the_parent(self) -> None:
        coverage = RequirementCoverage(
            requirement_id=REQ,
            fragments=(
                RequirementCoverage(
                    requirement_id="REQ-SYS-118/1", allocations=(_covered("010-FD"),)
                ),
                RequirementCoverage(
                    requirement_id="REQ-SYS-118/2", allocations=(_covered("030-AD"),)
                ),
            ),
        )
        assert coverage.is_covered is True

    def test_a_split_requirement_cannot_also_hold_allocations(self) -> None:
        # Two answers to one question: the fragments say one thing, the
        # parent's own allocations another.
        with pytest.raises(ValidationError, match="through its fragments"):
            RequirementCoverage(
                requirement_id=REQ,
                allocations=(_covered(),),
                fragments=(
                    RequirementCoverage(
                        requirement_id="REQ-SYS-118/1", allocations=(_covered(),)
                    ),
                ),
            )

    def test_coverage_is_not_a_settable_field(self) -> None:
        # R-310-096: a settable conclusion can disagree with its evidence.
        assert "is_covered" not in RequirementCoverage.model_fields
        assert "covered" not in RequirementCoverage.model_fields


@pytest.mark.unit
class TestContainerCoverage:
    def test_complete_when_nothing_is_uncovered(self) -> None:
        # R-310-120: the authoring workflow's completion condition.
        assert ContainerCoverage(
            container=ARCH, allocated=("REQ-1", "REQ-2")
        ).is_complete is True

    def test_incomplete_while_something_is_owed(self) -> None:
        assert ContainerCoverage(
            container=ARCH, allocated=("REQ-1",), uncovered=("REQ-1",)
        ).is_complete is False

    def test_weak_and_stale_are_reported_separately(self) -> None:
        # The workbench shows three different problems; collapsing them
        # would make "what do I fix first?" unanswerable.
        container = ContainerCoverage(
            container=ARCH, allocated=("REQ-1", "REQ-2", "REQ-3"),
            uncovered=("REQ-1",), weak=("REQ-2",), stale=("REQ-3",),
        )
        assert container.uncovered == ("REQ-1",)
        assert container.weak == ("REQ-2",)
        assert container.stale == ("REQ-3",)
