# =============================================================================
# File: test_object_locks_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_object_locks_verified.py
# Description: Integration tests for the object edit lease (310-SPEC §4.10)
#              against a real ArangoDB. Nothing here is meaningful without a
#              database: the whole design rests on one atomic AQL UPSERT.
#
#              Three properties this tier and only this tier can establish:
#                - two concurrent acquisitions of a free lock: exactly one wins;
#                - a lapsed lease reads as absent even though its row is still
#                  in the collection (Arango's TTL collector is periodic, so
#                  expiry SHALL be decided in code — R-310-190);
#                - an agent and a human contend for the same lease
#                  (R-310-191).
#
# @relation validates:R-310-190
# @relation validates:R-310-191
# @relation validates:R-310-193
# =============================================================================

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.objects.locks import (
    COLL_LOCKS,
    LockHeldError,
    LockManager,
    LockNotHeldError,
    lock_key,
)
from ay_platform_core.c5_requirements.objects.models import HolderKind
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)
PID = "adas-brake-ctrl"
OID = "OBJ-1124"
HUMAN = "o.mathieu"
AGENT = "agent:architect"


@pytest.fixture
def locks(arango_container: ArangoEndpoint) -> Iterator[LockManager]:
    db_name = f"c5_lock_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        manager = LockManager(db, lease_seconds=900)
        manager._ensure_collections_sync()
        yield manager
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(locks: LockManager) -> None:
    await locks.ensure_collections()
    await locks.ensure_collections()
    assert COLL_LOCKS in {c["name"] for c in locks._db.collections()}


@pytest.mark.asyncio
async def test_acquire_grants_a_bounded_lease(locks: LockManager) -> None:
    lock = await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    assert lock.holder == HUMAN
    assert lock.holder_kind is HolderKind.HUMAN
    assert lock.expires_at == NOW + timedelta(seconds=900)


@pytest.mark.asyncio
async def test_second_holder_is_refused_and_told_who_holds_it(
    locks: LockManager,
) -> None:
    """R-310-193 — an anonymous refusal is experienced as a malfunction."""
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)
    with pytest.raises(LockHeldError) as excinfo:
        await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    assert excinfo.value.lock.holder == AGENT
    assert AGENT in str(excinfo.value)


@pytest.mark.asyncio
async def test_agent_and_human_contend_for_the_same_lease(
    locks: LockManager,
) -> None:
    """R-310-191 — an agent redrafting while a reviewer edits loses updates."""
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    with pytest.raises(LockHeldError):
        await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)


@pytest.mark.asyncio
async def test_concurrent_acquisition_has_exactly_one_winner(
    locks: LockManager,
) -> None:
    """The reason acquisition is a single AQL statement."""
    contenders = [f"actor-{i}" for i in range(8)]
    results = await asyncio.gather(
        *(
            locks.acquire(PID, OID, who, HolderKind.HUMAN, now=NOW)
            for who in contenders
        ),
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    refusals = [r for r in results if isinstance(r, LockHeldError)]
    assert len(winners) == 1, f"{len(winners)} holders granted the same lease"
    assert len(refusals) == len(contenders) - 1
    held = await locks.get(PID, OID, now=NOW)
    assert held is not None
    assert held.holder == winners[0].holder


@pytest.mark.asyncio
async def test_holder_reacquiring_extends_its_own_lease(locks: LockManager) -> None:
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    later = NOW + timedelta(minutes=5)
    extended = await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=later)
    assert extended.expires_at == later + timedelta(seconds=900)


@pytest.mark.asyncio
async def test_lapsed_lease_reads_as_absent_while_its_row_survives(
    locks: LockManager,
) -> None:
    """R-310-190 — expiry is decided in code, never by the TTL collector.

    Arango's TTL collector runs periodically, so the row is still physically
    present right after expiry. Trusting the collector would let a lapsed
    lease keep blocking an edit for an unpredictable window.
    """
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    after = NOW + timedelta(seconds=901)

    assert locks._db.collection(COLL_LOCKS).get(lock_key(PID, OID)) is not None
    assert await locks.get(PID, OID, now=after) is None


@pytest.mark.asyncio
async def test_lapsed_lease_can_be_taken_over(locks: LockManager) -> None:
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    after = NOW + timedelta(seconds=901)
    taken = await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=after)
    assert taken.holder == AGENT
    assert taken.acquired_at == after


@pytest.mark.asyncio
async def test_renew_extends_a_live_lease(locks: LockManager) -> None:
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    later = NOW + timedelta(minutes=10)
    renewed = await locks.renew(PID, OID, HUMAN, now=later)
    assert renewed.expires_at == later + timedelta(seconds=900)


@pytest.mark.asyncio
async def test_renew_refuses_a_lapsed_lease(locks: LockManager) -> None:
    """Somebody else may have taken it over, so a lapsed lease is not renewable."""
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    with pytest.raises(LockNotHeldError):
        await locks.renew(PID, OID, HUMAN, now=NOW + timedelta(seconds=901))


@pytest.mark.asyncio
async def test_renew_refuses_a_foreign_lease(locks: LockManager) -> None:
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)
    with pytest.raises(LockNotHeldError):
        await locks.renew(PID, OID, HUMAN, now=NOW)


@pytest.mark.asyncio
async def test_release_frees_the_object(locks: LockManager) -> None:
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    assert await locks.release(PID, OID, HUMAN) is True
    assert await locks.get(PID, OID, now=NOW) is None
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)


@pytest.mark.asyncio
async def test_release_of_an_absent_lease_reports_false(locks: LockManager) -> None:
    assert await locks.release(PID, OID, HUMAN) is False


@pytest.mark.asyncio
async def test_release_refuses_a_foreign_lease(locks: LockManager) -> None:
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)
    with pytest.raises(LockNotHeldError):
        await locks.release(PID, OID, HUMAN)
    assert (await locks.get(PID, OID, now=NOW)) is not None


@pytest.mark.asyncio
async def test_force_release_ignores_the_holder(locks: LockManager) -> None:
    """R-310-193 — the container owner can always break a stuck lease."""
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)
    assert await locks.force_release(PID, OID) is True
    assert await locks.get(PID, OID, now=NOW) is None


@pytest.mark.asyncio
async def test_locks_are_scoped_per_object(locks: LockManager) -> None:
    await locks.acquire(PID, "OBJ-1", HUMAN, HolderKind.HUMAN, now=NOW)
    other = await locks.acquire(PID, "OBJ-2", AGENT, HolderKind.AGENT, now=NOW)
    assert other.object_id == "OBJ-2"


@pytest.mark.asyncio
async def test_locks_are_scoped_per_project(locks: LockManager) -> None:
    await locks.acquire("p1", OID, HUMAN, HolderKind.HUMAN, now=NOW)
    other = await locks.acquire("p2", OID, AGENT, HolderKind.AGENT, now=NOW)
    assert other.holder == AGENT


@pytest.mark.asyncio
async def test_assert_can_write_passes_when_unlocked(locks: LockManager) -> None:
    # An unlocked object is writable: the lock prevents wasted work, it is not
    # an authorisation gate. Correctness rests on the version check.
    await locks.assert_can_write(PID, OID, HUMAN, now=NOW)


@pytest.mark.asyncio
async def test_assert_can_write_passes_for_the_holder(locks: LockManager) -> None:
    await locks.acquire(PID, OID, HUMAN, HolderKind.HUMAN, now=NOW)
    await locks.assert_can_write(PID, OID, HUMAN, now=NOW)


@pytest.mark.asyncio
async def test_assert_can_write_blocks_a_foreign_writer(locks: LockManager) -> None:
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)
    with pytest.raises(LockHeldError):
        await locks.assert_can_write(PID, OID, HUMAN, now=NOW)


@pytest.mark.asyncio
async def test_assert_can_write_ignores_a_lapsed_lease(locks: LockManager) -> None:
    await locks.acquire(PID, OID, AGENT, HolderKind.AGENT, now=NOW)
    await locks.assert_can_write(PID, OID, HUMAN, now=NOW + timedelta(seconds=901))
