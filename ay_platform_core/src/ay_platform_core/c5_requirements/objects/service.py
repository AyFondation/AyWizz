# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/service.py
# Description: Object lifecycle for 310-SPEC-DOC-TRACEABILITY §4.1 / §4.7.
#              Ties the MinIO source of truth, the derived Arango index and
#              the edit lease together behind one facade.
#
#              THE RULE THIS MODULE EXISTS TO ENFORCE (R-310-009 / R-310-010):
#                - writing a working draft creates NO version;
#                - every review decision other than a rejection creates
#                  exactly one version, carrying its actor and timestamp;
#                - a rejection creates no version and leaves the object
#                  untouched — a refused draft never became content.
#
#              A confirm-unchanged therefore produces a content-identical
#              version. That is deliberate: the version chain is the decision
#              ledger, and "examined at v6, found unaffected" is the evidence
#              R-310-150 requires. A few KB against an envelope (R-310-303)
#              with an order of magnitude of headroom.
#
# @relation implements:R-310-009
# @relation implements:R-310-206
# @relation implements:R-310-010
# @relation implements:R-310-011
# @relation implements:R-310-124
# @relation implements:R-310-192
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

from .locks import LockManager
from .models import (
    DocObject,
    DocObjectPublic,
    HolderKind,
    ObjectLock,
    ObjectReviewRequest,
    ObjectType,
    ProducedBy,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
    WorkingDraft,
    WorkingDraftWrite,
    advances_version,
    state_for_decision,
)
from .repository import ObjectRepository
from .storage import ObjectStorage, validate_scope


class ObjectNotFoundError(LookupError):
    """Raised when an object does not exist in the container."""


class ObjectConflictError(RuntimeError):
    """Raised when a write states a version other than the current one.

    This is the correctness guarantee of R-310-192: the edit lease prevents
    wasted work, but leases expire, so every write re-states what it believes
    it is modifying.
    """

    def __init__(self, object_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"{object_id} is at v{actual}, caller expected v{expected}"
        )
        self.expected = expected
        self.actual = actual


class NoDraftError(RuntimeError):
    """Raised when a review names a draft that does not exist."""


#: How many iterations one negotiation on one object may produce before the
#: run is flagged (R-310-206). Twelve is generous for a convergent exchange
#: and clearly abnormal for a loop; a module default, overridden by
#: `C5_DRAFT_ITERATION_CAP`.
DEFAULT_DRAFT_ITERATION_CAP = 12


class DraftIterationCapError(RuntimeError):
    """Raised when one negotiation exceeded its iteration cap (`R-310-206`).

    An ANOMALY, not a conflict: iterations no longer cost storage
    (`R-310-009`), so the cap exists to surface an agent that rewrites one
    paragraph forty times without converging — a defect in the agent or the
    prompt, which costs tokens on every attempt. Carrying the count and the
    cap is what makes it actionable rather than merely annoying.
    """

    def __init__(self, object_id: str, negotiation_id: str, cap: int) -> None:
        self.object_id = object_id
        self.negotiation_id = negotiation_id
        self.cap = cap
        super().__init__(
            f"negotiation {negotiation_id!r} on {object_id!r} reached its "
            f"iteration cap of {cap} without converging. Iterations are free "
            "to store but not to produce, so this is reported as a run "
            "anomaly rather than silently permitted (R-310-206). Resolve the "
            "draft — accept, reject or confirm unchanged — before continuing."
        )


class ObjectService:
    """Create, negotiate and review document objects.

    Args:
        storage: MinIO source of truth.
        repository: Derived Arango index.
        locks: Edit lease manager.
        draft_iteration_cap: How many iterations one negotiation on one
            object may produce before it is reported as a run anomaly
            (`R-310-206`). Defaulted rather than required so every existing
            caller keeps working; configured from
            `C5_DRAFT_ITERATION_CAP`.
    """

    def __init__(
        self,
        storage: ObjectStorage,
        repository: ObjectRepository,
        locks: LockManager,
        draft_iteration_cap: int = DEFAULT_DRAFT_ITERATION_CAP,
    ) -> None:
        self._storage = storage
        self._repo = repository
        self._locks = locks
        self._iteration_cap = draft_iteration_cap

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(
        self, project_id: str, container: str, object_id: str
    ) -> DocObjectPublic:
        """Return one object with its draft and lock status.

        Raises:
            ObjectNotFoundError: When no such object exists.
        """
        obj = await self._read(project_id, container, object_id)
        draft = await self._storage.get_draft(project_id, container, object_id)
        lock = await self._locks.get(project_id, object_id)
        return DocObjectPublic.from_object(
            obj, has_working_draft=draft is not None, lock=lock
        )

    async def get_stored(
        self, project_id: str, container: str, object_id: str
    ) -> DocObject:
        """Return the stored object itself, for in-process callers."""
        return await self._read(project_id, container, object_id)

    async def list_container(
        self, project_id: str, container: str
    ) -> list[DocObjectPublic]:
        """Return a container's objects in reading order.

        Order comes from the index (R-310-002); bodies come from MinIO, so a
        stale index can misorder but can never serve stale content.
        """
        validate_scope(project_id, container)
        rows = await self._repo.list_container(project_id, container)
        result: list[DocObjectPublic] = []
        for row in rows:
            result.append(await self.get(project_id, container, row["object_id"]))
        return result

    async def get_version(
        self, project_id: str, container: str, object_id: str, version: int
    ) -> DocObject:
        """Return one retained version of an object (R-310-202)."""
        validate_scope(project_id, container)
        try:
            return await self._storage.get_version(
                project_id, container, object_id, version
            )
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(f"{object_id}@v{version}") from exc

    async def list_versions(
        self, project_id: str, container: str, object_id: str
    ) -> list[int]:
        """Return every retained version number for an object, ascending."""
        validate_scope(project_id, container)
        return await self._storage.list_versions(project_id, container, object_id)

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    async def create(
        self,
        project_id: str,
        container: str,
        *,
        object_id: str,
        object_type: ObjectType,
        actor: str,
        ordinal: int,
        body: str | None = None,
        notation: str | None = None,
        parent: str | None = None,
        produced_by: ProducedBy | None = None,
        cycle_version: int | None = None,
        now: datetime | None = None,
    ) -> DocObject:
        """Materialise a newly proposed object at v1.

        The object is `proposed` and carries no review record: an agent's
        proposal is not yet a decision. It acquires one at its first review.

        Raises:
            ObjectConflictError: When the identifier is already taken. Object
                identifiers are never reused (R-310-005), so this is a caller
                error rather than an overwrite to be performed silently.
        """
        moment = now or datetime.now(UTC)
        validate_scope(project_id, container)
        if await self._repo.get(project_id, object_id) is not None:
            raise ObjectConflictError(object_id, 0, 1)

        obj = DocObject(
            object_id=object_id,
            project_id=project_id,
            container=container,
            type=object_type,
            body=body,
            notation=notation,
            parent=parent,
            ordinal=ordinal,
            version=1,
            review_state=ReviewState.PROPOSED,
            last_review=None,
            produced_by=produced_by,
            cycle_version=cycle_version,
            created_at=moment,
            created_by=actor,
            updated_at=moment,
            updated_by=actor,
        )
        await self._persist(obj)
        return obj

    # ------------------------------------------------------------------
    # Negotiation — R-310-009
    # ------------------------------------------------------------------

    async def write_draft(
        self,
        project_id: str,
        container: str,
        object_id: str,
        payload: WorkingDraftWrite,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> WorkingDraft:
        """Write an iteration of a negotiation. Creates no version.

        Raises:
            ObjectNotFoundError: When the object does not exist.
            ObjectConflictError: When `base_version` is not the current one.
            LockHeldError: When somebody else holds the edit lease.
        """
        moment = now or datetime.now(UTC)
        obj = await self._read(project_id, container, object_id)
        await self._locks.assert_can_write(project_id, object_id, actor, now=moment)
        if payload.base_version != obj.version:
            raise ObjectConflictError(object_id, payload.base_version, obj.version)

        previous = await self._storage.get_draft(project_id, container, object_id)
        # The iteration counter restarts with each negotiation: it measures
        # how long THIS conversation took to converge, which is what the
        # R-310-206 anomaly cap is about.
        iteration = (
            previous.iteration + 1
            if previous is not None and previous.negotiation_id == payload.negotiation_id
            else 1
        )
        # R-310-206: surfaced, not silently permitted. Checked BEFORE the
        # write, so the refused iteration is not stored — otherwise the
        # anomaly would be reported and the work kept, which teaches a
        # caller that the cap is advisory.
        if iteration > self._iteration_cap:
            raise DraftIterationCapError(
                object_id, payload.negotiation_id, self._iteration_cap
            )

        draft = WorkingDraft(
            object_id=object_id,
            project_id=project_id,
            container=container,
            type=payload.type,
            body=payload.body,
            notation=payload.notation,
            negotiation_id=payload.negotiation_id,
            base_version=payload.base_version,
            iteration=iteration,
            written_at=moment,
            written_by=actor,
        )
        await self._storage.put_draft(draft)
        return draft

    async def get_draft(
        self, project_id: str, container: str, object_id: str
    ) -> WorkingDraft | None:
        """Return the unresolved working draft, or None."""
        validate_scope(project_id, container)
        return await self._storage.get_draft(project_id, container, object_id)

    # ------------------------------------------------------------------
    # Review — R-310-010
    # ------------------------------------------------------------------

    async def review(
        self,
        project_id: str,
        container: str,
        object_id: str,
        request: ObjectReviewRequest,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> DocObject:
        """Resolve a negotiation with a review decision.

        Accept and confirm-unchanged create exactly one new version carrying
        the decision. Rejection creates none and leaves the object untouched:
        a refused draft never became content, and re-running the authoring
        step is scoped to this object alone (R-310-124).

        Returns:
            The object as it stands after the decision.

        Raises:
            ObjectNotFoundError: When the object does not exist.
            ObjectConflictError: When `expected_version` is not the current one.
            NoDraftError: When accepting with no draft to accept.
            LockHeldError: When somebody else holds the edit lease.
        """
        moment = now or datetime.now(UTC)
        obj = await self._read(project_id, container, object_id)
        await self._locks.assert_can_write(project_id, object_id, actor, now=moment)
        if request.expected_version != obj.version:
            raise ObjectConflictError(
                object_id, request.expected_version, obj.version
            )

        draft = await self._storage.get_draft(project_id, container, object_id)

        if not advances_version(request.decision):
            # Rejection: discard the draft, leave the object alone.
            await self._storage.delete_draft(project_id, container, object_id)
            return obj

        if request.decision is ReviewDecision.CONFIRM_UNCHANGED and draft is not None:
            raise NoDraftError(
                f"{object_id} has an unresolved draft; confirm-unchanged asserts "
                "no change is needed and SHALL NOT silently discard one"
            )

        body, notation, obj_type = (
            (draft.body, draft.notation, draft.type)
            if draft is not None
            else (obj.body, obj.notation, obj.type)
        )

        reviewed = DocObject(
            object_id=obj.object_id,
            project_id=obj.project_id,
            container=obj.container,
            type=obj_type,
            body=body,
            notation=notation,
            parent=obj.parent,
            ordinal=obj.ordinal,
            version=obj.version + 1,
            review_state=state_for_decision(request.decision),
            last_review=ReviewRecord(
                decision=request.decision,
                actor=actor,
                at=moment,
                justification=request.justification,
            ),
            produced_by=obj.produced_by,
            cycle_version=obj.cycle_version,
            created_at=obj.created_at,
            created_by=obj.created_by,
            updated_at=moment,
            updated_by=actor,
        )
        await self._persist(reviewed)
        await self._storage.delete_draft(project_id, container, object_id)
        return reviewed

    # ------------------------------------------------------------------
    # Locking passthrough — the router needs one entry point
    # ------------------------------------------------------------------

    async def acquire_lock(
        self,
        project_id: str,
        object_id: str,
        holder: str,
        holder_kind: HolderKind,
    ) -> ObjectLock:
        """Take the edit lease on an object (R-310-190)."""
        return await self._locks.acquire(project_id, object_id, holder, holder_kind)

    async def release_lock(
        self, project_id: str, object_id: str, holder: str
    ) -> bool:
        """Release the edit lease held by `holder`."""
        return await self._locks.release(project_id, object_id, holder)

    async def force_release_lock(self, project_id: str, object_id: str) -> bool:
        """Break a lease regardless of its holder (R-310-193).

        Authorisation — the spec reserves this to the container owner — is
        enforced at the route, not here.
        """
        return await self._locks.force_release(project_id, object_id)

    async def get_lock(self, project_id: str, object_id: str) -> ObjectLock | None:
        """Return the live edit lease on an object, or None."""
        return await self._locks.get(project_id, object_id)

    # ------------------------------------------------------------------
    # Rebuild — R-310-002
    # ------------------------------------------------------------------

    async def reindex_container(self, project_id: str, container: str) -> int:
        """Rebuild a container's index rows from MinIO.

        Reads the source of truth, not the index, so it repairs an index that
        is missing rows, holds stale ones, or is empty.

        Returns:
            The number of objects indexed.
        """
        validate_scope(project_id, container)
        object_ids = await self._storage.list_object_ids(project_id, container)
        objects = [
            await self._storage.get_object(project_id, container, oid)
            for oid in object_ids
        ]
        return await self._repo.rebuild_container(project_id, container, objects)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _read(
        self, project_id: str, container: str, object_id: str
    ) -> DocObject:
        validate_scope(project_id, container)
        try:
            return await self._storage.get_object(project_id, container, object_id)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(object_id) from exc

    async def _persist(self, obj: DocObject) -> None:
        # MinIO first: it is the source of truth, and an index row pointing at
        # a version MinIO does not hold would be a lie. The reverse — content
        # written but not yet indexed — is repaired by `reindex_container`.
        await self._storage.put_object(obj)
        await self._repo.upsert(obj)
