# =============================================================================
# File: repository.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/coverage/repository.py
# Description: ArangoDB storage for the traceability graph — 310-SPEC §4.4 /
#              §4.7, E-310-006.
#
#              TWO SHAPES, DELIBERATELY DIFFERENT:
#
#              `req_allocations` is a DOCUMENT collection, not an edge.
#              E-310-006 calls it an edge, which cannot work: an edge needs
#              two vertex documents and a *container* is not a document — it
#              is declared by the cycle (R-310-021). Creating container
#              vertices to satisfy the shape would duplicate cycle data in a
#              second place able to drift from it. The spec entity needs
#              correcting; the code states the truth.
#
#              `req_object_edges` IS a true edge collection: objects and
#              requirements are both stored documents, and the impact DAG of
#              §4.8 has to traverse it. It ALSO carries `object_id` and
#              `target_id` as plain fields so the coverage aggregation can
#              filter without requiring every vertex to exist yet — intake
#              (increment 4) is what materialises requirement vertices.
#
#              One collection holds all three allocation decisions —
#              allocation, out-of-project verdict, and return — because
#              "what was decided about where this requirement is answered"
#              is one question, and splitting it across collections would
#              make R-310-065's audit ("nothing was decided at all") a join.
#
# @relation implements:R-310-064
# @relation implements:R-310-065
# @relation implements:R-310-068
# @relation implements:R-310-069
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar, cast

from .models import (
    Allocation,
    AllocationRejection,
    CoverageLink,
    OutOfProjectVerdict,
)

_T = TypeVar("_T")

COLL_ALLOCATIONS = "req_allocations"
COLL_COVERAGE = "req_object_edges"

#: Vertex collections the coverage edge points between.
_OBJECTS = "req_objects"
_ENTITIES = "req_entities"


class DecisionKind(StrEnum):
    """What an allocation-collection row records."""

    ALLOCATION = "allocation"
    OUT_OF_PROJECT = "out-of-project"
    RETURN = "return"


def allocation_key(project_id: str, requirement_id: str, container: str) -> str:
    """Return the key of one requirement-to-container allocation."""
    return f"a:{project_id}:{requirement_id}:{container}"


def verdict_key(project_id: str, requirement_id: str) -> str:
    """Return the key of a requirement's out-of-project verdict."""
    return f"v:{project_id}:{requirement_id}"


def return_key(
    project_id: str, requirement_id: str, container: str, ordinal: int
) -> str:
    """Return the key of the n-th return of one allocation.

    Returns accumulate rather than replace: R-310-069 escalates on the
    SECOND return, which is only countable if the first still exists.
    """
    return f"r:{project_id}:{requirement_id}:{container}:{ordinal}"


def coverage_key(project_id: str, object_id: str, target_id: str) -> str:
    """Return the key of one coverage link."""
    return f"{project_id}:{object_id}:{target_id}"


def to_allocation_row(allocation: Allocation) -> dict[str, Any]:
    """Project an allocation onto its stored row."""
    return {
        "_key": allocation_key(
            allocation.project_id, allocation.requirement_id, allocation.container
        ),
        "kind": DecisionKind.ALLOCATION.value,
        "project_id": allocation.project_id,
        "requirement_id": allocation.requirement_id,
        "container": allocation.container,
        "scope_id": allocation.scope_id,
        "justification": allocation.justification,
        "state": allocation.state.value,
        "criticality": allocation.criticality,
        "decomposition_rationale": allocation.decomposition_rationale,
        "actor": allocation.actor,
        "at": allocation.at.isoformat(),
    }


def to_verdict_row(verdict: OutOfProjectVerdict) -> dict[str, Any]:
    """Project an out-of-project verdict onto its stored row."""
    return {
        "_key": verdict_key(verdict.project_id, verdict.requirement_id),
        "kind": DecisionKind.OUT_OF_PROJECT.value,
        "project_id": verdict.project_id,
        "requirement_id": verdict.requirement_id,
        "justification": verdict.justification,
        "actor": verdict.actor,
        "at": verdict.at.isoformat(),
    }


def to_return_row(rejection: AllocationRejection, ordinal: int) -> dict[str, Any]:
    """Project a returned allocation onto its stored row."""
    return {
        "_key": return_key(
            rejection.project_id,
            rejection.requirement_id,
            rejection.container,
            ordinal,
        ),
        "kind": DecisionKind.RETURN.value,
        "project_id": rejection.project_id,
        "requirement_id": rejection.requirement_id,
        "container": rejection.container,
        "reason": rejection.reason.value,
        "detail": rejection.detail,
        "ordinal": ordinal,
        "actor": rejection.actor,
        "at": rejection.at.isoformat(),
    }


def to_coverage_row(link: CoverageLink) -> dict[str, Any]:
    """Project a coverage link onto its stored edge.

    `_from` / `_to` make it traversable for the impact DAG of §4.8;
    `object_id` / `target_id` make it filterable for the coverage matrix
    without requiring the target vertex to exist yet.
    """
    return {
        "_key": coverage_key(link.project_id, link.object_id, link.target_id),
        "_from": f"{_OBJECTS}/{link.project_id}:{link.object_id}",
        "_to": f"{_ENTITIES}/{link.project_id}:{link.target_id}",
        "project_id": link.project_id,
        "object_id": link.object_id,
        "container": link.container,
        "target_id": link.target_id,
        "pinned_version": link.pinned_version,
        "strength": link.strength.value,
        "state": link.state.value,
        "actor": link.actor,
        "at": link.at.isoformat(),
    }


class CoverageRepository:
    """Allocation decisions and coverage links.

    Args:
        db: A python-arango `Database` handle.
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run(self, func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        async with self._get_lock():
            return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_ALLOCATIONS not in existing:
            self._db.create_collection(COLL_ALLOCATIONS)
        if COLL_COVERAGE not in existing:
            self._db.create_collection(COLL_COVERAGE, edge=True)

        allocations = self._db.collection(COLL_ALLOCATIONS)
        # "What was decided about this requirement" is the hot read.
        allocations.add_index(
            {"type": "persistent", "fields": ["project_id", "requirement_id", "kind"]}
        )
        # "What does this container owe" — the authoring completion check.
        allocations.add_index(
            {"type": "persistent", "fields": ["project_id", "container", "kind"]}
        )
        coverage = self._db.collection(COLL_COVERAGE)
        coverage.add_index(
            {"type": "persistent", "fields": ["project_id", "target_id"]}
        )
        coverage.add_index(
            {"type": "persistent", "fields": ["project_id", "container"]}
        )

    async def ensure_collections(self) -> None:
        """Create the traceability collections and their indexes if absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Allocation decisions
    # ------------------------------------------------------------------

    def _upsert_sync(self, collection: str, row: dict[str, Any]) -> None:
        self._db.collection(collection).insert(row, overwrite=True)

    async def put_allocation(self, allocation: Allocation) -> None:
        """Record (or re-record) one allocation."""
        await self._run(self._upsert_sync, COLL_ALLOCATIONS, to_allocation_row(allocation))

    async def put_verdict(self, verdict: OutOfProjectVerdict) -> None:
        """Record an out-of-project verdict for a requirement."""
        await self._run(self._upsert_sync, COLL_ALLOCATIONS, to_verdict_row(verdict))

    async def put_return(self, rejection: AllocationRejection) -> int:
        """Record a returned allocation and report how many times it happened.

        Returns:
            The 1-based ordinal of this return. `R-310-069` escalates on the
            second, which is only countable because returns accumulate.
        """
        previous = await self.count_returns(
            rejection.project_id, rejection.requirement_id, rejection.container
        )
        ordinal = previous + 1
        await self._run(
            self._upsert_sync, COLL_ALLOCATIONS, to_return_row(rejection, ordinal)
        )
        return ordinal

    def _count_returns_sync(
        self, project_id: str, requirement_id: str, container: str
    ) -> int:
        aql = """
        FOR d IN req_allocations
            FILTER d.project_id == @pid
               AND d.requirement_id == @rid
               AND d.container == @container
               AND d.kind == "return"
            COLLECT WITH COUNT INTO n
            RETURN n
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={"pid": project_id, "rid": requirement_id, "container": container},
        )
        rows = list(cursor)
        return int(rows[0]) if rows else 0

    async def count_returns(
        self, project_id: str, requirement_id: str, container: str
    ) -> int:
        """Return how many times this allocation has been returned."""
        return await self._run(
            self._count_returns_sync, project_id, requirement_id, container
        )

    def _returned_containers_sync(
        self, project_id: str, requirement_id: str
    ) -> list[str]:
        aql = """
        FOR d IN req_allocations
            FILTER d.project_id == @pid
               AND d.requirement_id == @rid
               AND d.kind == "return"
            RETURN DISTINCT d.container
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "rid": requirement_id}
        )
        return sorted(cast(list[str], list(cursor)))

    async def returned_containers(
        self, project_id: str, requirement_id: str
    ) -> list[str]:
        """Return every container that has refused this requirement.

        Re-allocation excludes these (R-310-069): proposing a target that
        already said no is how the loop starts.
        """
        return await self._run(
            self._returned_containers_sync, project_id, requirement_id
        )

    def _delete_sync(self, collection: str, key: str) -> bool:
        coll = self._db.collection(collection)
        if not coll.has(key):
            return False
        coll.delete(key)
        return True

    async def drop_allocation(
        self, project_id: str, requirement_id: str, container: str
    ) -> bool:
        """Remove one allocation, leaving its return history intact.

        The returns are what the next allocation reads (R-310-068); dropping
        them with the allocation would make the replay uninformed.
        """
        return await self._run(
            self._delete_sync,
            COLL_ALLOCATIONS,
            allocation_key(project_id, requirement_id, container),
        )

    def _decisions_sync(
        self, project_id: str, requirement_id: str | None, container: str | None
    ) -> list[dict[str, Any]]:
        aql = """
        FOR d IN req_allocations
            FILTER d.project_id == @pid
               AND (@rid == null OR d.requirement_id == @rid)
               AND (@container == null OR d.container == @container)
            SORT d.requirement_id ASC, d.container ASC, d.kind ASC
            RETURN d
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={"pid": project_id, "rid": requirement_id, "container": container},
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def decisions(
        self,
        project_id: str,
        requirement_id: str | None = None,
        container: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return allocation decisions, optionally narrowed."""
        return await self._run(
            self._decisions_sync, project_id, requirement_id, container
        )

    def _unallocated_sync(self, project_id: str, candidates: list[str]) -> list[str]:
        aql = """
        LET decided = (
            FOR d IN req_allocations
                FILTER d.project_id == @pid
                   AND (d.kind == "allocation" OR d.kind == "out-of-project")
                RETURN DISTINCT d.requirement_id
        )
        FOR r IN @candidates
            FILTER r NOT IN decided
            RETURN r
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "candidates": candidates}
        )
        return sorted(cast(list[str], list(cursor)))

    async def unallocated(self, project_id: str, candidates: list[str]) -> list[str]:
        """Return which of `candidates` have neither an allocation nor a verdict.

        This is R-310-065's audit: the failure it closes is silence, so the
        question is asked of the candidate set rather than of what happens
        to be stored.
        """
        if not candidates:
            return []
        return await self._run(self._unallocated_sync, project_id, candidates)

    # ------------------------------------------------------------------
    # Coverage links
    # ------------------------------------------------------------------

    async def put_coverage(self, link: CoverageLink) -> None:
        """Record (or re-record) one coverage link."""
        await self._run(self._upsert_sync, COLL_COVERAGE, to_coverage_row(link))

    async def drop_coverage(
        self, project_id: str, object_id: str, target_id: str
    ) -> bool:
        """Remove one coverage link."""
        return await self._run(
            self._delete_sync,
            COLL_COVERAGE,
            coverage_key(project_id, object_id, target_id),
        )

    def _coverage_sync(
        self, project_id: str, target_id: str | None, container: str | None
    ) -> list[dict[str, Any]]:
        aql = """
        FOR e IN req_object_edges
            FILTER e.project_id == @pid
               AND (@target == null OR e.target_id == @target)
               AND (@container == null OR e.container == @container)
            SORT e.target_id ASC, e.object_id ASC
            RETURN e
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "target": target_id, "container": container}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def coverage_links(
        self,
        project_id: str,
        target_id: str | None = None,
        container: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return coverage links, optionally narrowed by target or container."""
        return await self._run(self._coverage_sync, project_id, target_id, container)

    def _links_to_targets_sync(
        self, project_id: str, target_ids: list[str]
    ) -> list[dict[str, Any]]:
        aql = """
        FOR e IN req_object_edges
            FILTER e.project_id == @pid AND e.target_id IN @targets
            SORT e.target_id ASC, e.object_id ASC
            RETURN e
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "targets": target_ids}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def links_to_targets(
        self, project_id: str, target_ids: frozenset[str]
    ) -> list[dict[str, Any]]:
        """Return every coverage link pointing at any of `target_ids`.

        The batched form of `coverage_links(target_id=...)`, added for the
        impact traversal of §4.8: one query per layer of the walk instead
        of one per node, so a 30 000-requirement corpus costs the DAG's
        depth in round trips rather than its width.

        Deliberately named for what it reads rather than what the caller
        does with it: this module owns `req_object_edges` and should not
        acquire change-absorption vocabulary. Inverting a link into a
        propagation step is the caller's claim, not the storage's.
        """
        if not target_ids:
            return []
        return await self._run(
            self._links_to_targets_sync, project_id, sorted(target_ids)
        )

    def _covering_objects_sync(self, project_id: str) -> list[dict[str, Any]]:
        aql = """
        FOR e IN req_object_edges
            FILTER e.project_id == @pid
            COLLECT target = e.target_id, container = e.container
            INTO links = { object_id: e.object_id, strength: e.strength,
                           state: e.state, pinned_version: e.pinned_version }
            RETURN { target_id: target, container: container, links: links }
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def coverage_by_target(self, project_id: str) -> list[dict[str, Any]]:
        """Return every coverage link grouped by target and container.

        One query for the whole matrix: asking per requirement would be
        thousands of round trips at the volume of R-310-300.
        """
        return await self._run(self._covering_objects_sync, project_id)
