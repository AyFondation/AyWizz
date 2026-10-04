# =============================================================================
# File: locks.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/locks.py
# Description: Lease-based exclusive locks on document objects
#              (310-SPEC-DOC-TRACEABILITY §4.10).
#
#              Held in ArangoDB rather than MinIO because acquisition needs an
#              atomic compare-and-set, which object storage does not offer.
#              One AQL UPSERT performs the whole decision — read, expiry
#              check, and write — so two callers racing for a free lock cannot
#              both win.
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
        # One statement decides everything, so two callers racing for a free
        # lock cannot both observe it as free. A lapsed lease is taken over;
        # the current holder re-acquiring extends its own lease.
        # The whole conditional is parenthesised on purpose: AQL's `IN` is
        # also the array-membership operator, so an unparenthesised
        # `… : {} IN req_object_locks` parses as a membership test and the
        # UPSERT loses its target collection.
        aql = """
        UPSERT { _key: @key }
        INSERT @doc
        UPDATE (
            (OLD.expires_at_ts <= @now_ts OR OLD.holder == @holder) ? @doc : {}
        )
        IN req_object_locks
        RETURN {
            row: NEW,
            granted: (OLD == null
                      OR OLD.expires_at_ts <= @now_ts
                      OR OLD.holder == @holder)
        }
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "key": doc["_key"],
                "doc": doc,
                "holder": holder,
                "now_ts": now.timestamp(),
            },
        )
        # The UPSERT always returns exactly one row, so consuming the first is
        # total — an empty cursor here would mean Arango broke its contract.
        result = next(iter(cursor))
        return cast(dict[str, Any], result["row"]), bool(result["granted"])

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
