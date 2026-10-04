# =============================================================================
# File: test_object_storage.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_object_storage.py
# Description: Unit tests for ObjectStorage (310-SPEC §4.1 / §4.11) against an
#              in-memory double of the MinIO facade. The double implements the
#              same three primitives the real facade exposes; the MinIO client
#              itself is exercised at the integration tier.
#
#              Load-bearing assertions: writing an object appends an immutable
#              version snapshot (R-310-202), a draft touches no version
#              (R-310-009), and no API exists to delete a version (R-310-204).
#
# @relation validates:R-310-001
# @relation validates:R-310-009
# @relation validates:R-310-202
# @relation validates:R-310-203
# @relation validates:R-310-204
# @relation validates:R-310-205
# =============================================================================

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import pytest

from ay_platform_core.c5_requirements.objects.models import (
    DocObject,
    ObjectType,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
    WorkingDraft,
)
from ay_platform_core.c5_requirements.objects.storage import (
    ObjectPathError,
    ObjectStorage,
)
from ay_platform_core.c5_requirements.storage.minio_storage import (
    ObjectMetadata,
    StorageError,
)

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)
PID = "adas-brake-ctrl"
CONTAINER = "030-ARCHITECTURE-DESIGN"
PREFIX = f"projects/{PID}/docs/{CONTAINER}/objects/"


class FakeStorage:
    """In-memory stand-in for the MinIO facade.

    Only the three primitives ObjectStorage composes are implemented. This is
    a dependency double, not a double of the system under test (§10.2 #2).
    """

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def put_document(
        self, path: str, data: bytes, content_type: str = "text/markdown"
    ) -> None:
        self.blobs[path] = data
        self.content_types[path] = content_type

    async def get_document(self, path: str) -> bytes:
        if path not in self.blobs:
            raise FileNotFoundError(path)
        return self.blobs[path]

    async def delete_document(self, path: str) -> None:
        if path not in self.blobs:
            raise FileNotFoundError(path)
        del self.blobs[path]

    async def list_objects(self, prefix: str) -> list[ObjectMetadata]:
        return [
            ObjectMetadata(path=p, size=len(d), etag="")
            for p, d in sorted(self.blobs.items())
            if p.startswith(prefix)
        ]


@pytest.fixture
def store() -> tuple[ObjectStorage, FakeStorage]:
    fake = FakeStorage()
    return ObjectStorage(fake), fake  # type: ignore[arg-type]


def _object(**overrides: object) -> DocObject:
    payload: dict[str, object] = {
        "object_id": "OBJ-1120",
        "project_id": PID,
        "container": CONTAINER,
        "type": ObjectType.PARAGRAPH,
        "body": "Ramp limited to 140 Nm/s.",
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


@pytest.mark.unit
class TestPathConstruction:
    def test_object_path(self) -> None:
        assert ObjectStorage.object_path(PID, CONTAINER, "OBJ-1120") == (
            f"{PREFIX}OBJ-1120.json"
        )

    def test_draft_path_is_distinct_from_object_path(self) -> None:
        assert ObjectStorage.draft_path(PID, CONTAINER, "OBJ-1120") == (
            f"{PREFIX}OBJ-1120.draft.json"
        )

    def test_version_path_is_compressed(self) -> None:
        assert ObjectStorage.version_path(PID, CONTAINER, "OBJ-1120", 12) == (
            f"{PREFIX}_versions/OBJ-1120@v12.json.gz"
        )

    def test_version_zero_rejected(self) -> None:
        with pytest.raises(ObjectPathError, match="SHALL be >= 1"):
            ObjectStorage.version_path(PID, CONTAINER, "OBJ-1120", 0)

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".hidden"])
    def test_traversal_in_container_rejected(self, bad: str) -> None:
        with pytest.raises(ObjectPathError, match="container slug"):
            ObjectStorage.object_path(PID, bad, "OBJ-1120")

    @pytest.mark.parametrize("bad", ["../escape", "a/b", ""])
    def test_traversal_in_project_rejected(self, bad: str) -> None:
        with pytest.raises(ObjectPathError, match="project id"):
            ObjectStorage.object_path(bad, CONTAINER, "OBJ-1120")

    def test_malformed_object_id_rejected(self) -> None:
        with pytest.raises(ObjectPathError, match="object id"):
            ObjectStorage.object_path(PID, CONTAINER, "obj/1120")


@pytest.mark.unit
class TestObjectPersistence:
    @pytest.mark.asyncio
    async def test_put_then_get_round_trip(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        obj = _object()
        await s.put_object(obj)
        assert await s.get_object(PID, CONTAINER, "OBJ-1120") == obj

    @pytest.mark.asyncio
    async def test_put_appends_a_version_snapshot(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        # R-310-202: every version is retained, not just the current one.
        s, fake = store
        await s.put_object(_object())
        assert f"{PREFIX}_versions/OBJ-1120@v1.json.gz" in fake.blobs

    @pytest.mark.asyncio
    async def test_version_snapshot_is_gzip(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        # R-310-205: complete compressed documents, never deltas — so a
        # snapshot must decompress to a whole object on its own.
        s, fake = store
        await s.put_object(_object())
        path = f"{PREFIX}_versions/OBJ-1120@v1.json.gz"
        assert fake.content_types[path] == "application/gzip"
        decoded = json.loads(gzip.decompress(fake.blobs[path]))
        assert decoded["object_id"] == "OBJ-1120"
        assert decoded["body"] == "Ramp limited to 140 Nm/s."

    @pytest.mark.asyncio
    async def test_current_object_is_plain_json(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, fake = store
        await s.put_object(_object())
        assert fake.content_types[f"{PREFIX}OBJ-1120.json"] == "application/json"

    @pytest.mark.asyncio
    async def test_earlier_versions_survive_a_later_write(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.put_object(_object(version=1, body="first"))
        await s.put_object(
            _object(
                version=2,
                body="second",
                review_state=ReviewState.ACCEPTED,
                last_review=ReviewRecord(
                    decision=ReviewDecision.ACCEPT, actor="o.mathieu", at=NOW
                ),
            )
        )
        v1 = await s.get_version(PID, CONTAINER, "OBJ-1120", 1)
        v2 = await s.get_version(PID, CONTAINER, "OBJ-1120", 2)
        assert v1.body == "first"
        assert v2.body == "second"
        current = await s.get_object(PID, CONTAINER, "OBJ-1120")
        assert current.version == 2

    @pytest.mark.asyncio
    async def test_list_versions_ascending(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        record = ReviewRecord(decision=ReviewDecision.ACCEPT, actor="o.m", at=NOW)
        for v in (1, 2, 3):
            await s.put_object(
                _object(version=v, last_review=None if v == 1 else record)
            )
        assert await s.list_versions(PID, CONTAINER, "OBJ-1120") == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_list_versions_does_not_bleed_across_similar_ids(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        # OBJ-11 is a prefix of OBJ-110: a naive prefix match would merge them.
        s, _ = store
        await s.put_object(_object(object_id="OBJ-11"))
        await s.put_object(_object(object_id="OBJ-110"))
        assert await s.list_versions(PID, CONTAINER, "OBJ-11") == [1]
        assert await s.list_versions(PID, CONTAINER, "OBJ-110") == [1]

    @pytest.mark.asyncio
    async def test_missing_object_raises_file_not_found(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        with pytest.raises(FileNotFoundError):
            await s.get_object(PID, CONTAINER, "OBJ-9999")

    @pytest.mark.asyncio
    async def test_corrupt_payload_is_attributed_to_its_path(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, fake = store
        fake.blobs[f"{PREFIX}OBJ-1120.json"] = b'{"object_id": "OBJ-1120"}'
        with pytest.raises(StorageError, match=r"Corrupt DocObject payload at .*OBJ-1120"):
            await s.get_object(PID, CONTAINER, "OBJ-1120")

    @pytest.mark.asyncio
    async def test_list_object_ids_excludes_drafts_and_versions(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.put_object(_object(object_id="OBJ-1118", ordinal=1))
        await s.put_object(_object(object_id="OBJ-1120", ordinal=2))
        await s.put_draft(_draft(object_id="OBJ-1120"))
        assert await s.list_object_ids(PID, CONTAINER) == ["OBJ-1118", "OBJ-1120"]


@pytest.mark.unit
class TestWorkingDraft:
    @pytest.mark.asyncio
    async def test_draft_round_trip(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        draft = _draft()
        await s.put_draft(draft)
        assert await s.get_draft(PID, CONTAINER, "OBJ-1120") == draft

    @pytest.mark.asyncio
    async def test_draft_creates_no_version(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        # R-310-009: this is the whole point — negotiation iterations are not
        # versions, so the version chain stays a history of decisions.
        s, fake = store
        await s.put_draft(_draft())
        assert not [p for p in fake.blobs if "_versions/" in p]

    @pytest.mark.asyncio
    async def test_iterating_overwrites_one_draft(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, fake = store
        await s.put_draft(_draft(iteration=1, body="first attempt"))
        await s.put_draft(_draft(iteration=2, body="second attempt"))
        drafts = [p for p in fake.blobs if p.endswith(".draft.json")]
        assert len(drafts) == 1
        current = await s.get_draft(PID, CONTAINER, "OBJ-1120")
        assert current is not None
        assert current.iteration == 2
        assert current.body == "second attempt"

    @pytest.mark.asyncio
    async def test_absent_draft_returns_none(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        assert await s.get_draft(PID, CONTAINER, "OBJ-1120") is None

    @pytest.mark.asyncio
    async def test_delete_draft_removes_it(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        # R-310-203: the draft is discarded once its negotiation resolves.
        s, _ = store
        await s.put_draft(_draft())
        await s.delete_draft(PID, CONTAINER, "OBJ-1120")
        assert await s.get_draft(PID, CONTAINER, "OBJ-1120") is None

    @pytest.mark.asyncio
    async def test_delete_draft_is_idempotent(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.delete_draft(PID, CONTAINER, "OBJ-1120")
        await s.delete_draft(PID, CONTAINER, "OBJ-1120")

    @pytest.mark.asyncio
    async def test_deleting_a_draft_leaves_the_object_intact(
        self, store: tuple[ObjectStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.put_object(_object())
        await s.put_draft(_draft())
        await s.delete_draft(PID, CONTAINER, "OBJ-1120")
        assert (await s.get_object(PID, CONTAINER, "OBJ-1120")).version == 1


@pytest.mark.unit
class TestRetentionIsStructural:
    """R-310-202 / R-310-204 — retention is enforced by absence of API."""

    def test_no_version_deletion_operation_exists(self) -> None:
        # The absence IS the enforcement: a delete_version() would make
        # R-310-204 (a baselined version is never deleted) a runtime policy
        # rather than a structural guarantee.
        forbidden = {
            name
            for name in dir(ObjectStorage)
            if "version" in name.lower()
            and any(verb in name.lower() for verb in ("delete", "remove", "purge"))
        }
        assert not forbidden, f"version-destroying API surfaced: {forbidden}"

    def test_no_compaction_operation_exists(self) -> None:
        forbidden = {n for n in dir(ObjectStorage) if "compact" in n.lower()}
        assert not forbidden, f"compaction API surfaced: {forbidden}"
