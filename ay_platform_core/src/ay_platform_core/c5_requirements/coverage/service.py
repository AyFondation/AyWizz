# =============================================================================
# File: service.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/coverage/service.py
# Description: Allocation and coverage rules — 310-SPEC §4.4 / §4.7.
#
#              THE RULES THIS MODULE ENFORCES, and what each one is guarding:
#
#              R-310-066  An allocation cites the scope of the container that
#                         justifies it, and is refused BEFORE a reviewer sees
#                         it when the citation names no such scope. Without
#                         it an agent allocates by lexical similarity — a
#                         requirement mentioning a bus protocol lands in the
#                         technical design although it is an architecture or
#                         a security requirement.
#              R-310-067  Criticality propagates to every container unless a
#                         justified decomposition is recorded. A tool that
#                         treated every decomposition as a violation would
#                         contradict the safety standard and be worked around.
#              R-310-069  Re-allocation excludes containers that already
#                         refused, and the second return escalates. Two owners
#                         can otherwise return the same requirement to each
#                         other indefinitely, with a budget attached.
#              R-310-121  A covers edge is refused when its target is not
#                         allocated to the source object's container: an
#                         object cannot answer something nobody asked it.
#              R-310-145  Staleness is computed against the target's current
#                         version at read time, never stored.
#
#              The current-version lookup is INJECTED rather than reached for:
#              requirement versions live outside this module, and a callable
#              keeps the dependency explicit and the rules testable without a
#              requirements corpus.
#
# @relation implements:R-310-065
# @relation implements:R-310-066
# @relation implements:R-310-067
# @relation implements:R-310-068
# @relation implements:R-310-069
# @relation implements:R-310-120
# @relation implements:R-310-121
# @relation implements:R-310-122
# @relation implements:R-310-145
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..objects.models import ReviewState
from ..process.service import ProcessService
from .models import (
    ACCEPTED_STATES,
    Allocation,
    AllocationCoverage,
    AllocationRejection,
    ContainerCoverage,
    CoverageLink,
    CoverageStrength,
    OutOfProjectVerdict,
    RequirementCoverage,
    ReturnReason,
    SpeculativeMarking,
    is_fragment,
    parent_requirement,
)
from .repository import CoverageRepository

#: Resolves the current version of a coverage target. Returns None when the
#: target is unknown, which is itself reportable.
VersionLookup = Callable[[str, str], Awaitable[int | None]]

#: (project_id, object_id) -> the object's review state, or None when it
#: is not a stored object (a supplied requirement, say). Injected for the
#: same reason as `VersionLookup`: the object corpus is a different
#: package, and reaching into it here would couple two surfaces that the
#: rebuildable-index rule keeps separate.
StateLookup = Callable[[str, str], Awaitable[ReviewState | None]]


class AllocationRefusedError(RuntimeError):
    """Raised when an allocation cannot be proposed as stated."""


class CoverageRefusedError(RuntimeError):
    """Raised when a coverage link cannot be recorded as stated."""


@dataclass(frozen=True, slots=True)
class ReturnOutcome:
    """What happened when a container owner returned an allocation."""

    ordinal: int
    escalated: bool
    """True when this return reached the arbitration threshold (R-310-069)."""

    next_exclusions: tuple[str, ...]
    """Containers a re-allocation SHALL NOT propose again."""


#: The return that stops trying and asks a human instead (R-310-069).
ESCALATION_THRESHOLD = 2


class CoverageService:
    """Allocate requirements, record coverage, and compute the matrix.

    Args:
        repository: Traceability graph storage.
        process: Resolves the published cycle a project runs on.
        current_version: Resolves a coverage target's current version.
        review_state_of: Resolves a coverage target's review state. Optional
            because it is only needed by the speculative marking of
            `R-310-177`; when absent, nothing is reported speculative rather
            than everything being reported so. Defaulting to "unknown" and
            then treating unknown as unaccepted would mark the whole corpus
            speculative on a misconfiguration, which is the loud-but-useless
            kind of failure.
    """

    def __init__(
        self,
        repository: CoverageRepository,
        process: ProcessService,
        current_version: VersionLookup,
        review_state_of: StateLookup | None = None,
    ) -> None:
        self._repo = repository
        self._process = process
        self._version = current_version
        self._state = review_state_of

    # ------------------------------------------------------------------
    # Allocation — R-310-065 / R-310-066 / R-310-067 / R-310-069
    # ------------------------------------------------------------------

    async def allocate(
        self,
        tenant_id: str,
        project_id: str,
        cycle_id: str,
        *,
        requirement_id: str,
        container: str,
        scope_id: str,
        justification: str,
        actor: str,
        criticality: str | None = None,
        inherited_criticality: str | None = None,
        decomposition_rationale: str | None = None,
        override_exclusion: bool = False,
        now: datetime | None = None,
    ) -> Allocation:
        """Propose one allocation, refusing it if the process does not allow it.

        Raises:
            AllocationRefusedError: When the cited scope does not belong to
                the container, when the container already refused this
                requirement, or when criticality is lowered without a
                recorded decomposition.
        """
        cycle = await self._process.resolve_cycle(tenant_id, project_id, cycle_id)
        if cycle is None:
            raise AllocationRefusedError(
                f"no published cycle {cycle_id!r} applies to {project_id!r}"
            )
        spec = cycle.container(container)
        if spec is None:
            raise AllocationRefusedError(
                f"{cycle_id}@v{cycle.version} declares no container {container!r}"
            )

        # R-310-066: the citation must be THIS container's scope. Citing a
        # neighbour's scope is the same failure as citing none — the
        # justification does not support the target.
        if spec.scope_id != scope_id:
            raise AllocationRefusedError(
                f"{scope_id!r} is not the scope of {container!r} "
                f"(which declares {spec.scope_id!r}) — an allocation SHALL "
                "cite the scope that justifies it (R-310-066)"
            )

        # R-310-069: proposing a target that already said no is how the loop
        # starts. A human may still force it, deliberately.
        if not override_exclusion:
            refused = await self._repo.returned_containers(project_id, requirement_id)
            if container in refused:
                raise AllocationRefusedError(
                    f"{container!r} already returned {requirement_id!r}; "
                    "re-allocation excludes it (R-310-069)"
                )

        # R-310-067: uniform propagation is the safe default; a lower level
        # is allowed only as a recorded, justified decomposition.
        effective = criticality if criticality is not None else inherited_criticality
        if (
            inherited_criticality is not None
            and effective != inherited_criticality
            and not (decomposition_rationale or "").strip()
        ):
            raise AllocationRefusedError(
                f"criticality {effective!r} differs from the requirement's "
                f"{inherited_criticality!r} without a recorded decomposition "
                "(R-310-067)"
            )

        allocation = Allocation(
            requirement_id=requirement_id,
            project_id=project_id,
            container=container,
            scope_id=scope_id,
            justification=justification,
            state=ReviewState.PROPOSED,
            criticality=effective,
            decomposition_rationale=decomposition_rationale,
            actor=actor,
            at=now or datetime.now(UTC),
        )
        await self._repo.put_allocation(allocation)
        return allocation

    async def accept_allocation(
        self,
        project_id: str,
        requirement_id: str,
        container: str,
        *,
        actor: str,
        auto: bool = False,
        now: datetime | None = None,
    ) -> Allocation:
        """Accept a proposed allocation, individually or by cluster.

        `auto=True` records cluster acceptance as `auto-accepted`, which is a
        distinct state on purpose (R-310-007): a coverage figure must be able
        to say how much of it was never examined individually.
        """
        rows = await self._repo.decisions(project_id, requirement_id, container)
        current = next((r for r in rows if r["kind"] == "allocation"), None)
        if current is None:
            raise AllocationRefusedError(
                f"no allocation of {requirement_id!r} to {container!r} to accept"
            )
        allocation = _allocation_from_row(current).model_copy(
            update={
                "state": (
                    ReviewState.AUTO_ACCEPTED if auto else ReviewState.ACCEPTED
                ),
                "actor": actor,
                "at": now or datetime.now(UTC),
            }
        )
        await self._repo.put_allocation(allocation)
        return allocation

    async def record_out_of_project(
        self,
        project_id: str,
        requirement_id: str,
        *,
        justification: str,
        actor: str,
        now: datetime | None = None,
    ) -> OutOfProjectVerdict:
        """Record that a requirement is not this project's to answer.

        R-310-065's escape valve: the alternative is an agent silently
        omitting what it cannot place, and an omission cannot be reviewed.
        """
        verdict = OutOfProjectVerdict(
            requirement_id=requirement_id,
            project_id=project_id,
            justification=justification,
            actor=actor,
            at=now or datetime.now(UTC),
        )
        await self._repo.put_verdict(verdict)
        return verdict

    async def return_allocation(
        self,
        project_id: str,
        requirement_id: str,
        container: str,
        *,
        reason: ReturnReason,
        detail: str,
        actor: str,
        now: datetime | None = None,
    ) -> ReturnOutcome:
        """Return an allocation to be re-decided (R-310-068).

        The allocation is dropped but its return history is kept: the returns
        are what the next allocation reads, so erasing them would make the
        replay uninformed and the same target would be proposed again.
        """
        rejection = AllocationRejection(
            requirement_id=requirement_id,
            project_id=project_id,
            container=container,
            reason=reason,
            detail=detail,
            actor=actor,
            at=now or datetime.now(UTC),
        )
        ordinal = await self._repo.put_return(rejection)
        await self._repo.drop_allocation(project_id, requirement_id, container)
        exclusions = await self._repo.returned_containers(project_id, requirement_id)
        return ReturnOutcome(
            ordinal=ordinal,
            escalated=ordinal >= ESCALATION_THRESHOLD,
            next_exclusions=tuple(exclusions),
        )

    async def audit_unallocated(
        self, project_id: str, candidates: list[str]
    ) -> list[str]:
        """Return which candidate requirements were never decided about.

        R-310-065's audit. Asked of the requirements that EXIST rather than
        of the rows that happen to be stored, because the failure it closes
        is an omission and a query over stored rows cannot see one.
        """
        return await self._repo.unallocated(project_id, candidates)

    # ------------------------------------------------------------------
    # Coverage — R-310-121 / R-310-122
    # ------------------------------------------------------------------

    async def cover(
        self,
        project_id: str,
        *,
        object_id: str,
        container: str,
        target_id: str,
        actor: str,
        strength: CoverageStrength = CoverageStrength.COVERED,
        now: datetime | None = None,
    ) -> CoverageLink:
        """Record that an object answers a target, pinning the target version.

        Raises:
            CoverageRefusedError: When the target is not allocated to this
                container (R-310-121), or when it has no current version to
                pin — a pin against nothing could never go stale.
        """
        allocated = await self._is_allocated(project_id, target_id, container)
        if not allocated:
            raise CoverageRefusedError(
                f"{target_id!r} is not allocated to {container!r}; an object "
                "cannot answer something nobody asked it (R-310-121)"
            )
        version = await self._version(project_id, target_id)
        if version is None:
            raise CoverageRefusedError(
                f"{target_id!r} has no current version to pin (R-310-145)"
            )
        link = CoverageLink(
            object_id=object_id,
            project_id=project_id,
            container=container,
            target_id=target_id,
            pinned_version=version,
            strength=strength,
            state=ReviewState.PROPOSED,
            actor=actor,
            at=now or datetime.now(UTC),
        )
        await self._repo.put_coverage(link)
        return link

    async def _is_allocated(
        self, project_id: str, target_id: str, container: str
    ) -> bool:
        rows = await self._repo.decisions(project_id, target_id, container)
        return any(r["kind"] == "allocation" for r in rows)

    # ------------------------------------------------------------------
    # Reads — the matrix
    # ------------------------------------------------------------------

    async def requirement_coverage(
        self, project_id: str, requirement_id: str, *, fragments: list[str] | None = None
    ) -> RequirementCoverage:
        """Return the aggregated coverage of one requirement.

        Args:
            project_id: Owning project.
            requirement_id: The requirement to report on.
            fragments: Its fragment ids when it was split. Supplied rather
                than discovered because splitting belongs to increment 4;
                when present, coverage is the aggregate of the fragments and
                the requirement's own allocations are not consulted
                (R-310-096).
        """
        if fragments:
            return RequirementCoverage(
                requirement_id=requirement_id,
                fragments=tuple(
                    [
                        await self.requirement_coverage(project_id, f)
                        for f in fragments
                    ]
                ),
            )

        decisions = await self._repo.decisions(project_id, requirement_id)
        if any(d["kind"] == "out-of-project" for d in decisions):
            return RequirementCoverage(
                requirement_id=requirement_id, out_of_project=True
            )

        links = await self._repo.coverage_links(project_id, requirement_id)
        current = await self._version(project_id, requirement_id)

        allocations: list[AllocationCoverage] = []
        for row in decisions:
            if row["kind"] != "allocation":
                continue
            allocations.append(
                _classify(
                    row["container"], requirement_id, row["state"], links, current
                )
            )
        return RequirementCoverage(
            requirement_id=requirement_id, allocations=tuple(allocations)
        )

    async def container_coverage(
        self, project_id: str, container: str
    ) -> ContainerCoverage:
        """Return what a container owes and what it has delivered (R-310-120)."""
        decisions = await self._repo.decisions(project_id, container=container)
        links = await self._repo.coverage_links(project_id, container=container)

        allocated: list[str] = []
        uncovered: list[str] = []
        weak: list[str] = []
        stale: list[str] = []

        for row in decisions:
            if row["kind"] != "allocation":
                continue
            requirement = row["requirement_id"]
            allocated.append(requirement)
            current = await self._version(project_id, requirement)
            classified = _classify(
                container, requirement, row["state"], links, current
            )
            if not classified.is_covered:
                uncovered.append(requirement)
            if classified.weak_objects:
                weak.append(requirement)
            if classified.stale_objects:
                stale.append(requirement)

        return ContainerCoverage(
            container=container,
            allocated=tuple(sorted(allocated)),
            uncovered=tuple(sorted(uncovered)),
            weak=tuple(sorted(weak)),
            stale=tuple(sorted(stale)),
        )

    async def suspect_links(self, project_id: str) -> list[dict[str, Any]]:
        """Return every coverage link whose target has moved past its pin.

        R-310-145, computed: a stored flag would drift the moment a target
        changed without it being updated, which is the failure the rule
        exists to prevent.
        """
        suspect: list[dict[str, Any]] = []
        for row in await self._repo.coverage_links(project_id):
            current = await self._version(project_id, row["target_id"])
            if current is not None and current > int(row["pinned_version"]):
                suspect.append({**row, "current_version": current})
        return suspect

    # ------------------------------------------------------------------
    # Speculative provenance — R-310-177 v2
    # ------------------------------------------------------------------

    async def speculative_objects(
        self, project_id: str, container: str | None = None
    ) -> tuple[SpeculativeMarking, ...]:
        """Return every object built on an upstream nobody has accepted.

        Computed from the graph on each call, never read from a flag: an
        object stops being speculative the moment its last unaccepted
        upstream is accepted, and no write happens at that moment to
        update a column. The same argument as `suspect_links` above.

        Args:
            project_id: Owning project.
            container: Narrow to one container when given.

        Returns:
            One marking per speculative object, ordered by object id. An
            object whose every upstream is accepted is ABSENT rather than
            present-and-false: the caller wants the review queue, not the
            corpus.
        """
        if self._state is None:
            return ()

        by_object: dict[str, list[dict[str, Any]]] = {}
        for row in await self._repo.coverage_links(project_id, container=container):
            by_object.setdefault(row["object_id"], []).append(row)

        markings: list[SpeculativeMarking] = []
        for object_id in sorted(by_object):
            rows = by_object[object_id]
            unaccepted: list[str] = []
            advanced: list[str] = []
            for row in rows:
                target = row["target_id"]
                state = await self._state(project_id, target)
                if state is None or state in ACCEPTED_STATES:
                    continue
                unaccepted.append(target)
                current = await self._version(project_id, target)
                if current is not None and current > int(row["pinned_version"]):
                    advanced.append(target)
            if not unaccepted:
                continue
            markings.append(
                SpeculativeMarking(
                    object_id=object_id,
                    container=rows[0]["container"],
                    unaccepted_targets=tuple(sorted(set(unaccepted))),
                    advanced_targets=tuple(sorted(set(advanced))),
                )
            )
        return tuple(markings)

    async def objects_to_stale(
        self, project_id: str, container: str | None = None
    ) -> tuple[str, ...]:
        """Return the objects whose review state must be set to `stale`.

        The second clause of `R-310-177`: a speculative object whose
        unaccepted upstream has changed answers a version of something that
        no longer exists and was never agreed to. The transition itself is
        a review-state write owned by the object surface, so this reports
        WHICH objects need it rather than performing it — one owner per
        piece of state (R-100-012).
        """
        return tuple(
            marking.object_id
            for marking in await self.speculative_objects(project_id, container)
            if marking.must_become_stale
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify(
    container: str,
    target_id: str,
    allocation_state: str,
    links: list[dict[str, Any]],
    current_version: int | None,
) -> AllocationCoverage:
    """Sort a container's coverage links into covering, weak and stale.

    A link counts as covering only when it is neither weak nor stale: a weak
    object cites without answering (R-310-122) and a stale one answers a
    version that has moved (R-310-145). Either way the allocation is not
    satisfied, and reporting it as covered is what turns a matrix green and
    untrustworthy.
    """
    covering: list[str] = []
    weak: list[str] = []
    stale: list[str] = []
    matched: list[CoverageLink] = []
    for link in links:
        # BOTH filters live here on purpose. Filtering only by container let
        # a container-wide link set be attributed to every requirement in it,
        # so a requirement with no coverage of its own reported as covered by
        # an object answering a different one.
        if link["container"] != container or link["target_id"] != target_id:
            continue
        # Kept alongside the id tuples: `R-500-019` needs the link's own
        # `state`, which no id can carry. See `AllocationCoverage.links`.
        matched.append(_coverage_link_from_row(link))
        object_id = link["object_id"]
        is_stale = current_version is not None and current_version > int(
            link["pinned_version"]
        )
        if link["strength"] == CoverageStrength.WEAK.value:
            weak.append(object_id)
        elif is_stale:
            stale.append(object_id)
        else:
            covering.append(object_id)
        if is_stale and link["strength"] == CoverageStrength.WEAK.value:
            stale.append(object_id)
    return AllocationCoverage(
        container=container,
        allocation_state=ReviewState(allocation_state),
        covering_objects=tuple(sorted(covering)),
        weak_objects=tuple(sorted(weak)),
        stale_objects=tuple(sorted(set(stale))),
        links=tuple(sorted(matched, key=lambda link: link.object_id)),
    )


def _coverage_link_from_row(row: dict[str, Any]) -> CoverageLink:
    """Rebuild a `CoverageLink` from a `req_object_edges` document.

    The AQL does `RETURN e`, so the row also carries Arango's `_key` /
    `_id` / `_rev` / `_from` / `_to`; `CoverageLink` is `extra="forbid"`,
    so the fields are named explicitly rather than splatted.
    """
    return CoverageLink(
        object_id=row["object_id"],
        project_id=row["project_id"],
        container=row["container"],
        target_id=row["target_id"],
        pinned_version=int(row["pinned_version"]),
        strength=CoverageStrength(row["strength"]),
        state=ReviewState(row["state"]),
        actor=row["actor"],
        at=datetime.fromisoformat(row["at"]),
    )


def _allocation_from_row(row: dict[str, Any]) -> Allocation:
    return Allocation(
        requirement_id=row["requirement_id"],
        project_id=row["project_id"],
        container=row["container"],
        scope_id=row["scope_id"],
        justification=row["justification"],
        state=ReviewState(row["state"]),
        criticality=row.get("criticality"),
        decomposition_rationale=row.get("decomposition_rationale"),
        actor=row["actor"],
        at=datetime.fromisoformat(row["at"]),
    )


__all__ = [
    "ESCALATION_THRESHOLD",
    "AllocationRefusedError",
    "CoverageRefusedError",
    "CoverageService",
    "ReturnOutcome",
    "VersionLookup",
    "is_fragment",
    "parent_requirement",
]
