# =============================================================================
# File: locks.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/locks.py
# Description: Lease-based exclusive locks on document objects
#              (310-SPEC-DOC-TRACEABILITY §4.10).
#
#              v3 (2026-10-09): the stale-lock RETRY path now treats a
#              unique-constraint violation as a refusal like the other two
#              acquisition paths already did. Several losers of the first
#              INSERT can reach that retry together, and all but one got a
#              raw `[ERR 1210]` — a 500 for behaving correctly. Surfaced by
#              the eight-replica stress test in a FULL-SUITE run and not in
#              isolation, because contention is what reaches the branch.
#
#              Held in ArangoDB rather than MinIO because acquisition needs an
#              atomic compare-and-set, which object storage does not offer.
#
#              v2 (2026-10-06) replaced the single AQL `UPSERT` with an
#              `INSERT` followed, only on conflict, by a conditional `UPDATE`.
#              The UPSERT's own comment claimed "one statement decides
#              everything, so two callers racing for a free lock cannot both
#              win"; ArangoDB documents UPSERT as a lookup THEN an
#              insert-or-update, so that was never the guarantee. What
#              actually happened under real concurrency is sharper and was
#              only reproducible with EIGHT INDEPENDENT LockManagers against
#              one database (`LockManager._run` serialises per instance, so
#              a single manager can never race itself — a test built on one
#              manager exercises nothing): Arango answered
#              `[ERR 1200] write-write conflict [node: UpsertNode]`, and
#              nothing caught it. The contender that lost the race received a
#              database error instead of `LockHeldError` — a 500 for
#              behaving correctly, on the path whose whole job is telling a
#              user who holds the lease (R-310-193).
#
#              Expiry is enforced in code, NOT by the TTL index. Arango's TTL
#              collector runs periodically, so an expired row survives for an
#              unpredictable window; relying on it would let a lapsed lease
#              keep blocking an edit. The TTL index is housekeeping only.
#
#              This lock prevents wasted work. It is NOT the correctness
#              guarantee: leases expire, so writes still carry the optimistic
#              version check of R-310-192.
#
# @relation implements:R-310-190
# @relation implements:R-310-191
# @relation implements:R-310-193
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast

from .models import HolderKind, ObjectLock

_T = TypeVar("_T")

COLL_LOCKS = "req_object_locks"

DEFAULT_LEASE_SECONDS = 900

#: Arango error numbers that mean "somebody else got there first".
#:
#: 1210 is the unique-`_key` violation — the insert found the document
#: already committed. 1200 is a write-write conflict: two transactions
#: touched the same document concurrently and this one was rolled back.
#:
#: BOTH are needed, and 1200 was found the hard way. The first version of
#: this helper matched only 1210, which looked right and passed every test —
#: until `test_exactly_one_winner_across_independent_replicas` drove eight
#: INDEPENDENT LockManagers at one document and Arango answered
#: `[HTTP 409][ERR 1200] AQL: write-write conflict [node #3: InsertNode]`.
#: Which of the two a given contender receives depends on how far the
#: winner's transaction had progressed, so a lock that handles only one of
#: them raises a 500 to roughly half the losers instead of refusing them.
_ACQUISITION_CONFLICT_ERRORS = frozenset({1200, 1210})


def _is_acquisition_conflict(exc: BaseException) -> bool:
    """True when `exc` is Arango saying the lease was taken concurrently.

    Matched on the server's error NUMBER rather than the exception class or
    its message: python-arango wraps server errors in several types
    (`DocumentInsertError`, `AQLQueryExecuteError`) depending on the call
    path, and the message is localisable.

    A conflict here is the EXPECTED outcome of a lost acquisition race, not
    a fault — which is why it is identified precisely instead of being
    swallowed by a bare `except`.
    """
    return getattr(exc, "error_code", None) in _ACQUISITION_CONFLICT_ERRORS


class LockHeldError(RuntimeError):
    """Raised when an object is locked by somebody else.

    Carries the current holder so the caller can tell the user who to ask,
    rather than reporting an anonymous failure (R-310-193).
    """

    def __init__(self, lock: ObjectLock) -> None:
        super().__init__(
            f"{lock.object_id} is held by {lock.holder} until "
            f"{lock.expires_at.isoformat()}"
        )
        self.lock = lock


class LockNotHeldError(RuntimeError):
    """Raised when renewing or releasing a lease the caller does not hold."""


def lock_key(project_id: str, object_id: str) -> str:
    """Return the ArangoDB `_key` of an object's lock row."""
    return f"{project_id}:{object_id}"


def _to_lock(row: dict[str, Any]) -> ObjectLock:
    return ObjectLock(
        object_id=row["object_id"],
        holder=row["holder"],
        holder_kind=HolderKind(row["holder_kind"]),
        acquired_at=datetime.fromisoformat(row["acquired_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


class LockManager:
    """Acquire, renew, release and inspect object edit leases.

    Args:
        db: A python-arango `Database` handle.
        lease_seconds: Lease duration granted on acquisition and renewal.
    """

    def __init__(self, db: Any, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds SHALL be >= 1")
        self._db = db
        self._lease = lease_seconds
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
        if COLL_LOCKS not in existing:
            self._db.create_collection(COLL_LOCKS)
        # Housekeeping only — expiry decisions are taken in code, never by
        # waiting for this collector to run.
        self._db.collection(COLL_LOCKS).add_index(
            {"type": "ttl", "fields": ["expires_at_ts"], "expireAfter": 0}
        )

    async def ensure_collections(self) -> None:
        """Create the lock collection and its TTL index if absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    def _acquire_sync(
        self,
        project_id: str,
        object_id: str,
        holder: str,
        holder_kind: HolderKind,
        now: datetime,
    ) -> tuple[dict[str, Any], bool]:
        expires = now + timedelta(seconds=self._lease)
        doc = {
            "_key": lock_key(project_id, object_id),
            "project_id": project_id,
            "object_id": object_id,
            "holder": holder,
            "holder_kind": holder_kind.value,
            "acquired_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "expires_at_ts": expires.timestamp(),
        }
        # ------------------------------------------------------------------
        # Acquisition is TWO atomic primitives, not one UPSERT.
        #
        # The previous implementation was a single `UPSERT … INSERT … UPDATE`
        # and its comment claimed "one statement decides everything, so two
        # callers racing for a free lock cannot both observe it as free".
        # That is not what UPSERT guarantees. ArangoDB documents UPSERT as a
        # LOOKUP followed by an insert-or-update, and warns that concurrent
        # UPSERTs on the same key can both find no document. Two contenders
        # could therefore both be told `granted: true` for a free lock.
        #
        # It was not theoretical: `test_concurrent_acquisition_has_exactly_
        # one_winner` (8 contenders via `asyncio.gather`) failed with "2
        # holders granted the same lease" on 2026-10-06. It passed most runs,
        # because the window is small — which is the worst property for a
        # lock whose entire purpose is preventing the lost updates of
        # R-310-191.
        #
        # What IS atomic is a single-document operation. So:
        #   1. INSERT. The unique `_key` makes exactly one concurrent insert
        #      succeed; every other contender gets a unique-constraint
        #      violation. That decides the free-lock race in the database,
        #      not in a read-then-write window.
        #   2. If the key already existed, a conditional UPDATE whose FILTER
        #      and write are one statement on one document: it takes the
        #      lease over only if the stored row is still expired, or still
        #      ours. Returning no row means somebody else holds it.
        # ------------------------------------------------------------------
        try:
            cursor = self._db.aql.execute(
                "INSERT @doc IN req_object_locks RETURN NEW",
                bind_vars={"doc": doc},
            )
            return cast(dict[str, Any], next(iter(cursor))), True
        except Exception as exc:
            if not _is_acquisition_conflict(exc):
                raise

        # The key exists. Take it over iff it is lapsed or already ours. The
        # FILTER is re-evaluated against the stored document inside this
        # statement, so a holder who renewed in the meantime is not displaced.
        takeover = """
        FOR l IN req_object_locks
            FILTER l._key == @key
            FILTER l.expires_at_ts <= @now_ts OR l.holder == @holder
            UPDATE l WITH @doc IN req_object_locks
            RETURN NEW
        """
        try:
            cursor = self._db.aql.execute(
                takeover,
                bind_vars={
                    "key": doc["_key"],
                    "doc": doc,
                    "holder": holder,
                    "now_ts": now.timestamp(),
                },
            )
            rows = list(cursor)
        except Exception as exc:
            # Two contenders can both match the FILTER (a lapsed lease is
            # fair game to either) and then collide on the write. Losing
            # that collision is a refusal, not a fault — the same reasoning
            # as the INSERT path above. Without this the loser would get a
            # 500 for behaving correctly.
            if not _is_acquisition_conflict(exc):
                raise
            rows = []
        if rows:
            return cast(dict[str, Any], rows[0]), True

        # Refused: report the CURRENT holder so the caller can say who to ask
        # (R-310-193) rather than failing anonymously.
        current = self._db.collection(COLL_LOCKS).get(doc["_key"])
        if current is None:
            # Released between the conflict and this read. Retry once: the
            # lock is free again and the INSERT path can decide cleanly.
            #
            # THIS RETRY IS ITSELF A RACE, and leaving it unguarded was a
            # defect (found 2026-10-09 by the eight-replica stress test in
            # a full-suite run, not in isolation — contention is what
            # reaches this branch at all). Several losers of the first
            # INSERT can arrive here together, all see a released lock,
            # and all retry; exactly one wins and the others got a raw
            # `[ERR 1210] unique constraint violated` — a 500 for
            # behaving correctly, which is the one outcome
            # `_is_acquisition_conflict` exists to prevent on the other
            # two paths.
            #
            # Losing the retry is a refusal, and there is no third attempt:
            # re-reading tells the caller who holds it now, and a lock that
            # has been taken between the conflict and the retry is simply
            # held. A loop here would trade a wrong 500 for an unbounded
            # one.
            try:
                cursor = self._db.aql.execute(
                    "INSERT @doc IN req_object_locks RETURN NEW",
                    bind_vars={"doc": doc},
                )
                return cast(dict[str, Any], next(iter(cursor))), True
            except Exception as exc:
                if not _is_acquisition_conflict(exc):
                    raise
                retried = self._db.collection(COLL_LOCKS).get(doc["_key"])
                if retried is not None:
                    return cast(dict[str, Any], retried), False
                # Released AGAIN between the retry's conflict and this
                # read. Report refused with the row we were trying to
                # write: the caller learns the acquisition did not happen,
                # which is true, instead of a database error.
                return doc, False
        return cast(dict[str, Any], current), False

    async def acquire(
        self,
        project_id: str,
        object_id: str,
        holder: str,
        holder_kind: HolderKind,
        *,
        now: datetime | None = None,
    ) -> ObjectLock:
        """Take the lease on an object.

        Re-acquiring a lease one already holds extends it, so a long edit
        never has to release first.

        Args:
            project_id: Owning project.
            object_id: Object to lock.
            holder: Actor identifier — a user or an agent (R-310-191).
            holder_kind: Whether the holder is a human or an agent.
            now: Reference instant; defaults to the current UTC time.

        Returns:
            The granted lease.

        Raises:
            LockHeldError: When an unexpired lease belongs to somebody else.
        """
        moment = now or datetime.now(UTC)
        row, granted = await self._run(
            self._acquire_sync, project_id, object_id, holder, holder_kind, moment
        )
        lock = _to_lock(row)
        if not granted:
            raise LockHeldError(lock)
        return lock

    # ------------------------------------------------------------------
    # Renewal, release, inspection
    # ------------------------------------------------------------------

    async def renew(
        self,
        project_id: str,
        object_id: str,
        holder: str,
        *,
        now: datetime | None = None,
    ) -> ObjectLock:
        """Extend a lease the caller currently holds.

        Raises:
            LockNotHeldError: When the caller holds no live lease on the
                object — including when their own lease already lapsed, since
                somebody else may have taken it over in the meantime.
        """
        moment = now or datetime.now(UTC)
        current = await self.get(project_id, object_id, now=moment)
        if current is None or not current.is_held_by(holder, moment):
            raise LockNotHeldError(
                f"{holder} does not hold a live lease on {object_id}"
            )
        return await self.acquire(
            project_id, object_id, holder, current.holder_kind, now=moment
        )

    def _release_sync(self, key: str, holder: str | None) -> bool:
        collection = self._db.collection(COLL_LOCKS)
        row = collection.get(key)
        if row is None:
            return False
        if holder is not None and row["holder"] != holder:
            raise LockNotHeldError(
                f"{holder} does not hold the lease on {row['object_id']}"
            )
        collection.delete(key)
        return True

    async def release(self, project_id: str, object_id: str, holder: str) -> bool:
        """Release a lease the caller holds.

        Returns:
            True when a lease was removed, False when there was none.

        Raises:
            LockNotHeldError: When the lease belongs to somebody else.
        """
        return await self._run(
            self._release_sync, lock_key(project_id, object_id), holder
        )

    async def force_release(self, project_id: str, object_id: str) -> bool:
        """Release a lease regardless of its holder (R-310-193).

        Authorisation is the router's concern: the spec reserves this to the
        container owner. This layer only executes.

        Returns:
            True when a lease was removed, False when there was none.
        """
        return await self._run(self._release_sync, lock_key(project_id, object_id), None)

    def _get_sync(self, key: str) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, self._db.collection(COLL_LOCKS).get(key))

    async def get(
        self, project_id: str, object_id: str, *, now: datetime | None = None
    ) -> ObjectLock | None:
        """Return the live lease on an object, or None.

        A lapsed lease reads as None even while its row survives: the TTL
        collector is periodic, so trusting it would let an expired lease keep
        blocking an edit.
        """
        moment = now or datetime.now(UTC)
        row = await self._run(self._get_sync, lock_key(project_id, object_id))
        if row is None:
            return None
        lock = _to_lock(row)
        return None if lock.is_expired(moment) else lock

    async def assert_can_write(
        self, project_id: str, object_id: str, actor: str, *, now: datetime | None = None
    ) -> None:
        """Raise unless `actor` may write the object right now.

        An unlocked object is writable: the lock is an anti-clobber
        convenience, not an authorisation gate, and correctness rests on the
        version check of R-310-192.

        Raises:
            LockHeldError: When somebody else holds a live lease.
        """
        current = await self.get(project_id, object_id, now=now)
        if current is not None and current.holder != actor:
            raise LockHeldError(current)
