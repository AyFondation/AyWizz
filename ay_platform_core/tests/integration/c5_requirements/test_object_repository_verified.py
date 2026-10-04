# =============================================================================
# File: test_object_repository_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_object_repository_verified.py
# Description: Integration tests for ObjectRepository against a real ArangoDB
#              (testcontainers). The unit tier covers the pure projection;
#              everything here needs a database to mean anything — AQL
#              correctness, index creation, upsert-over-existing-_rev, and the
#              rebuild path that makes the index disposable (R-310-002).
#
#              The load-bearing test is `test_index_is_rebuildable_from_scratch`:
#              R-310-002 claims a total loss of ArangoDB is an outage and not
#              data loss. That claim is only worth anything if it is executed.
#
# @relation validates:R-310-002
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.objects.models import (
    DocObject,
    ObjectType,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
)
from ay_platform_core.c5_requirements.objects.repository import (
    COLL_OBJECTS,
    ObjectRepository,
    index_key,
)
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)
PID = "adas-brake-ctrl"
ARCH = "030-ARCHITECTURE-DESIGN"
TECH = "050-TECHNICAL-DESIGN"

_ACCEPT = ReviewRecord(decision=ReviewDecision.ACCEPT, actor="o.mathieu", at=NOW)


def _object(**overrides: object) -> DocObject:
    payload: dict[str, object] = {
        "object_id": "OBJ-1120",
        "project_id": PID,
        "container": ARCH,
        "type": ObjectType.PARAGRAPH,
        "body": "The primary actuator shall deliver up to 140 Nm.",
        "ordinal": 3,
        "version": 1,
        "review_state": ReviewState.PROPOSED,
        "created_at": NOW,
        "created_by": "agent:architect",
        "updated_at": NOW,
        "updated_by": "agent:architect",
    }
    payload.update(overrides)
    return DocObject.model_validate(payload)


@pytest.fixture
def object_repo(arango_container: ArangoEndpoint) -> Iterator[ObjectRepository]:
    db_name = f"c5_obj_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ObjectRepository(db)
        repo._ensure_collections_sync()
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(object_repo: ObjectRepository) -> None:
    """Called on every lifespan start; a second call must not fail."""
    await object_repo.ensure_collections()
    await object_repo.ensure_collections()
    names = {c["name"] for c in object_repo._db.collections()}
    assert COLL_OBJECTS in names


@pytest.mark.asyncio
async def test_declared_indexes_exist(object_repo: ObjectRepository) -> None:
    indexes = object_repo._db.collection(COLL_OBJECTS).indexes()
    field_sets = [tuple(i["fields"]) for i in indexes]
    assert ("project_id", "container", "ordinal") in field_sets
    assert ("project_id", "review_state") in field_sets


@pytest.mark.asyncio
async def test_upsert_then_get(object_repo: ObjectRepository) -> None:
    await object_repo.upsert(_object())
    row = await object_repo.get(PID, "OBJ-1120")
    assert row is not None
    assert row["_key"] == index_key(PID, "OBJ-1120")
    assert row["container"] == ARCH
    assert row["review_state"] == "proposed"


@pytest.mark.asyncio
async def test_upsert_overwrites_without_revision_conflict(
    object_repo: ObjectRepository,
) -> None:
    """The reason `overwrite=True` is used: a stale _rev must not 412."""
    await object_repo.upsert(_object(version=1))
    await object_repo.upsert(
        _object(version=2, review_state=ReviewState.ACCEPTED, last_review=_ACCEPT)
    )
    row = await object_repo.get(PID, "OBJ-1120")
    assert row is not None
    assert row["version"] == 2
    assert row["review_state"] == "accepted"


@pytest.mark.asyncio
async def test_missing_object_returns_none(object_repo: ObjectRepository) -> None:
    assert await object_repo.get(PID, "OBJ-9999") is None


@pytest.mark.asyncio
async def test_list_container_is_ordered_by_position(
    object_repo: ObjectRepository,
) -> None:
    for oid, ordinal in (("OBJ-1126", 3), ("OBJ-1118", 1), ("OBJ-1120", 2)):
        await object_repo.upsert(_object(object_id=oid, ordinal=ordinal))
    rows = await object_repo.list_container(PID, ARCH)
    assert [r["object_id"] for r in rows] == ["OBJ-1118", "OBJ-1120", "OBJ-1126"]


@pytest.mark.asyncio
async def test_list_container_order_is_total_on_tied_ordinals(
    object_repo: ObjectRepository,
) -> None:
    """Two objects sharing an ordinal must still render deterministically."""
    for oid in ("OBJ-2000", "OBJ-1000", "OBJ-3000"):
        await object_repo.upsert(_object(object_id=oid, ordinal=5))
    first = [r["object_id"] for r in await object_repo.list_container(PID, ARCH)]
    second = [r["object_id"] for r in await object_repo.list_container(PID, ARCH)]
    assert first == second == ["OBJ-1000", "OBJ-2000", "OBJ-3000"]


@pytest.mark.asyncio
async def test_containers_do_not_leak_into_each_other(
    object_repo: ObjectRepository,
) -> None:
    await object_repo.upsert(_object(object_id="OBJ-1120", container=ARCH))
    await object_repo.upsert(_object(object_id="OBJ-2203", container=TECH))
    arch = await object_repo.list_container(PID, ARCH)
    tech = await object_repo.list_container(PID, TECH)
    assert [r["object_id"] for r in arch] == ["OBJ-1120"]
    assert [r["object_id"] for r in tech] == ["OBJ-2203"]


@pytest.mark.asyncio
async def test_projects_do_not_leak_into_each_other(
    object_repo: ObjectRepository,
) -> None:
    await object_repo.upsert(_object(object_id="OBJ-1", project_id="p1"))
    await object_repo.upsert(_object(object_id="OBJ-1", project_id="p2"))
    p1 = await object_repo.list_container("p1", ARCH)
    assert [r["project_id"] for r in p1] == ["p1"]


@pytest.mark.asyncio
async def test_list_by_review_state(object_repo: ObjectRepository) -> None:
    await object_repo.upsert(_object(object_id="OBJ-1", ordinal=1))
    await object_repo.upsert(
        _object(
            object_id="OBJ-2",
            ordinal=2,
            review_state=ReviewState.ACCEPTED,
            version=2,
            last_review=_ACCEPT,
        )
    )
    await object_repo.upsert(
        _object(
            object_id="OBJ-3",
            ordinal=3,
            review_state=ReviewState.AUTO_ACCEPTED,
            version=2,
            last_review=_ACCEPT,
        )
    )
    proposed = await object_repo.list_by_review_state(PID, "proposed")
    auto = await object_repo.list_by_review_state(PID, "auto-accepted")
    assert [r["object_id"] for r in proposed] == ["OBJ-1"]
    assert [r["object_id"] for r in auto] == ["OBJ-3"]


@pytest.mark.asyncio
async def test_count_by_review_state_separates_auto_from_accepted(
    object_repo: ObjectRepository,
) -> None:
    """R-310-007 / R-310-225 — the auto-accepted share must be recoverable."""
    await object_repo.upsert(_object(object_id="OBJ-1", ordinal=1))
    for oid in ("OBJ-2", "OBJ-3"):
        await object_repo.upsert(
            _object(
                object_id=oid,
                ordinal=2,
                review_state=ReviewState.ACCEPTED,
                version=2,
                last_review=_ACCEPT,
            )
        )
    for oid in ("OBJ-4", "OBJ-5", "OBJ-6"):
        await object_repo.upsert(
            _object(
                object_id=oid,
                ordinal=3,
                review_state=ReviewState.AUTO_ACCEPTED,
                version=2,
                last_review=_ACCEPT,
            )
        )
    counts = await object_repo.count_by_review_state(PID)
    assert counts == {"proposed": 1, "accepted": 2, "auto-accepted": 3}


@pytest.mark.asyncio
async def test_delete_reports_whether_a_row_existed(
    object_repo: ObjectRepository,
) -> None:
    await object_repo.upsert(_object())
    assert await object_repo.delete(PID, "OBJ-1120") is True
    assert await object_repo.delete(PID, "OBJ-1120") is False
    assert await object_repo.get(PID, "OBJ-1120") is None


@pytest.mark.asyncio
async def test_index_is_rebuildable_from_scratch(
    object_repo: ObjectRepository,
) -> None:
    """R-310-002 — a total index loss is an outage, not data loss.

    The claim is only worth something executed: the index is emptied
    completely, then rebuilt from what MinIO would hand back, and the result
    must be indistinguishable from the original.
    """
    objects = [
        _object(object_id="OBJ-1118", ordinal=1),
        _object(object_id="OBJ-1120", ordinal=2),
        _object(
            object_id="OBJ-1126",
            ordinal=3,
            review_state=ReviewState.ACCEPTED,
            version=4,
            last_review=_ACCEPT,
        ),
    ]
    for obj in objects:
        await object_repo.upsert(obj)
    before = await object_repo.list_container(PID, ARCH)

    # Simulate the loss: drop every row of the container.
    object_repo._db.collection(COLL_OBJECTS).truncate()
    assert await object_repo.list_container(PID, ARCH) == []

    written = await object_repo.rebuild_container(PID, ARCH, objects)
    after = await object_repo.list_container(PID, ARCH)

    assert written == 3
    assert [r["object_id"] for r in after] == [r["object_id"] for r in before]
    assert [r["version"] for r in after] == [r["version"] for r in before]
    assert [r["content_hash"] for r in after] == [r["content_hash"] for r in before]


@pytest.mark.asyncio
async def test_rebuild_drops_rows_absent_from_the_source(
    object_repo: ObjectRepository,
) -> None:
    """An object deleted in MinIO must not survive in the index."""
    await object_repo.upsert(_object(object_id="OBJ-1118", ordinal=1))
    await object_repo.upsert(_object(object_id="OBJ-1120", ordinal=2))

    await object_repo.rebuild_container(
        PID, ARCH, [_object(object_id="OBJ-1118", ordinal=1)]
    )

    rows = await object_repo.list_container(PID, ARCH)
    assert [r["object_id"] for r in rows] == ["OBJ-1118"]


@pytest.mark.asyncio
async def test_rebuild_leaves_other_containers_untouched(
    object_repo: ObjectRepository,
) -> None:
    await object_repo.upsert(_object(object_id="OBJ-2203", container=TECH))
    await object_repo.upsert(_object(object_id="OBJ-1120", container=ARCH))

    await object_repo.rebuild_container(PID, ARCH, [])

    assert await object_repo.list_container(PID, ARCH) == []
    assert len(await object_repo.list_container(PID, TECH)) == 1
