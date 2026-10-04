# =============================================================================
# File: test_object_storage_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_object_storage_verified.py
# Description: Storage-verified integration tests for ObjectStorage against a
#              real MinIO (testcontainers). Writes through ObjectStorage, then
#              opens the raw MinIO client to assert the persistence boundary.
#
#              What the unit tier CANNOT catch, and this tier SHALL:
#                - real recursive prefix listing returns the `_versions/`
#                  sub-tree too, so `list_object_ids` must filter it — an
#                  in-memory double with the same naive listing would agree
#                  with a wrong implementation;
#                - the version snapshot must be genuine gzip on the wire
#                  (magic bytes), not merely round-trippable by our own code;
#                - the stored media types must reach MinIO.
#
# @relation validates:R-310-001
# @relation validates:R-310-202
# @relation validates:R-310-203
# @relation validates:R-310-205
# =============================================================================

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from minio import Minio

from ay_platform_core.c5_requirements.objects.models import (
    DocObject,
    ObjectType,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
    WorkingDraft,
)
from ay_platform_core.c5_requirements.objects.storage import ObjectStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)
PID = "adas-brake-ctrl"
CONTAINER = "030-ARCHITECTURE-DESIGN"
PREFIX = f"projects/{PID}/docs/{CONTAINER}/objects/"

_ACCEPT = ReviewRecord(decision=ReviewDecision.ACCEPT, actor="o.mathieu", at=NOW)


def _object(**overrides: object) -> DocObject:
    payload: dict[str, object] = {
        "object_id": "OBJ-1120",
        "project_id": PID,
        "container": CONTAINER,
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


def _draft(**overrides: object) -> WorkingDraft:
    payload: dict[str, object] = {
        "object_id": "OBJ-1120",
        "project_id": PID,
        "container": CONTAINER,
        "type": ObjectType.PARAGRAPH,
        "body": "Ramp raised to 140 Nm/s per CR-2041.",
        "negotiation_id": "NEG-0031",
        "base_version": 1,
        "iteration": 1,
        "written_at": NOW,
        "written_by": "agent:architect",
    }
    payload.update(overrides)
    return WorkingDraft.model_validate(payload)


@pytest.fixture
def object_store(c5_storage: RequirementsStorage) -> Iterator[ObjectStorage]:
    yield ObjectStorage(c5_storage)


def _raw_client(storage: RequirementsStorage) -> tuple[Minio, str]:
    """Reach into the facade for the raw client + bucket.

    Deliberate: this tier exists to inspect the persistence boundary from
    outside the code under test.
    """
    return storage._client, storage._bucket


def _read_raw(storage: RequirementsStorage, path: str) -> bytes:
    client, bucket = _raw_client(storage)
    response = client.get_object(bucket, path)
    try:
        return bytes(response.read())
    finally:
        response.close()
        response.release_conn()


@pytest.mark.asyncio
async def test_object_lands_at_the_specified_path(
    object_store: ObjectStorage, c5_storage: RequirementsStorage
) -> None:
    """R-310-001 — E-310-001 fixes the layout; a drift must fail here."""
    await object_store.put_object(_object())

    raw = _read_raw(c5_storage, f"{PREFIX}OBJ-1120.json")
    decoded = json.loads(raw)
    assert decoded["object_id"] == "OBJ-1120"
    assert decoded["container"] == CONTAINER
    assert decoded["version"] == 1


@pytest.mark.asyncio
async def test_version_snapshot_is_real_gzip_on_the_wire(
    object_store: ObjectStorage, c5_storage: RequirementsStorage
) -> None:
    """R-310-205 — complete compressed documents, verified byte-level."""
    await object_store.put_object(_object())

    raw = _read_raw(c5_storage, f"{PREFIX}_versions/OBJ-1120@v1.json.gz")
    assert raw[:2] == b"\x1f\x8b", "snapshot is not gzip-framed"
    decoded = json.loads(gzip.decompress(raw))
    assert decoded["body"] == "The primary actuator shall deliver up to 140 Nm."


@pytest.mark.asyncio
async def test_media_types_reach_minio(
    object_store: ObjectStorage, c5_storage: RequirementsStorage
) -> None:
    await object_store.put_object(_object())
    client, bucket = _raw_client(c5_storage)

    current = client.stat_object(bucket, f"{PREFIX}OBJ-1120.json")
    snapshot = client.stat_object(bucket, f"{PREFIX}_versions/OBJ-1120@v1.json.gz")
    assert current.content_type == "application/json"
    assert snapshot.content_type == "application/gzip"


@pytest.mark.asyncio
async def test_version_chain_accumulates_and_nothing_is_lost(
    object_store: ObjectStorage,
) -> None:
    """R-310-202 — full retention across successive decisions."""
    await object_store.put_object(_object(version=1, body="first"))
    await object_store.put_object(
        _object(
            version=2,
            body="second",
            review_state=ReviewState.ACCEPTED,
            last_review=_ACCEPT,
        )
    )
    await object_store.put_object(
        _object(
            version=3,
            body="third",
            review_state=ReviewState.ACCEPTED,
            last_review=_ACCEPT,
        )
    )

    assert await object_store.list_versions(PID, CONTAINER, "OBJ-1120") == [1, 2, 3]
    bodies = [
        (await object_store.get_version(PID, CONTAINER, "OBJ-1120", v)).body
        for v in (1, 2, 3)
    ]
    assert bodies == ["first", "second", "third"]
    assert (await object_store.get_object(PID, CONTAINER, "OBJ-1120")).version == 3


@pytest.mark.asyncio
async def test_list_object_ids_filters_the_versions_subtree(
    object_store: ObjectStorage,
) -> None:
    """The unit double cannot prove this: real listing is recursive.

    MinIO's recursive prefix listing returns `_versions/...` keys under the
    same prefix as the current objects. An implementation that forgot to
    filter them would pass against a naive in-memory double and fail here.
    """
    await object_store.put_object(_object(object_id="OBJ-1118", ordinal=1))
    await object_store.put_object(
        _object(
            object_id="OBJ-1118",
            ordinal=1,
            version=2,
            review_state=ReviewState.ACCEPTED,
            last_review=_ACCEPT,
        )
    )
    await object_store.put_object(_object(object_id="OBJ-1120", ordinal=2))
    await object_store.put_draft(_draft(object_id="OBJ-1120"))

    assert await object_store.list_object_ids(PID, CONTAINER) == [
        "OBJ-1118",
        "OBJ-1120",
    ]


@pytest.mark.asyncio
async def test_similar_ids_do_not_share_a_version_chain(
    object_store: ObjectStorage,
) -> None:
    """`OBJ-11` is a string prefix of `OBJ-110`."""
    await object_store.put_object(_object(object_id="OBJ-11"))
    await object_store.put_object(
        _object(
            object_id="OBJ-11",
            version=2,
            review_state=ReviewState.ACCEPTED,
            last_review=_ACCEPT,
        )
    )
    await object_store.put_object(_object(object_id="OBJ-110"))

    assert await object_store.list_versions(PID, CONTAINER, "OBJ-11") == [1, 2]
    assert await object_store.list_versions(PID, CONTAINER, "OBJ-110") == [1]


@pytest.mark.asyncio
async def test_draft_lifecycle_leaves_no_version_behind(
    object_store: ObjectStorage,
) -> None:
    """R-310-009 / R-310-203 — iterate, then resolve, and no version appears."""
    await object_store.put_object(_object())
    for i in (1, 2, 3):
        await object_store.put_draft(_draft(iteration=i, body=f"attempt {i}"))

    live = await object_store.get_draft(PID, CONTAINER, "OBJ-1120")
    assert live is not None
    assert live.iteration == 3

    await object_store.delete_draft(PID, CONTAINER, "OBJ-1120")

    assert await object_store.get_draft(PID, CONTAINER, "OBJ-1120") is None
    # Three iterations, still exactly one version: the object's own v1.
    assert await object_store.list_versions(PID, CONTAINER, "OBJ-1120") == [1]


@pytest.mark.asyncio
async def test_missing_object_raises_file_not_found(
    object_store: ObjectStorage,
) -> None:
    with pytest.raises(FileNotFoundError):
        await object_store.get_object(PID, CONTAINER, "OBJ-9999")
