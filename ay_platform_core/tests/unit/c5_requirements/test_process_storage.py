# =============================================================================
# File: test_process_storage.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_process_storage.py
# Description: Unit tests for ProcessStorage and the ProcessRepository
#              projection, against an in-memory double of the MinIO facade.
#
#              The load-bearing assertion is that a sealed version cannot be
#              rewritten (R-310-023): there is no method that could, and
#              `seal` refuses an occupied slot. Without it, the
#              `produced_by: WF-nnn@vN` stamp on every object means nothing.
#
# @relation validates:R-310-020
# @relation validates:R-310-023
# @relation validates:R-310-040
# @relation validates:R-310-045
# =============================================================================

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from ay_platform_core.c5_requirements.process.models import (
    ContainerSpec,
    CycleDefinition,
    EntityStatus,
    StepKind,
    WorkflowCheck,
    WorkflowDefinition,
    WorkflowStep,
)
from ay_platform_core.c5_requirements.process.repository import (
    index_key,
    scope_key,
    to_cycle_row,
    to_workflow_row,
)
from ay_platform_core.c5_requirements.process.storage import (
    AlreadyPublishedError,
    ProcessPathError,
    ProcessStorage,
)
from ay_platform_core.c5_requirements.storage.minio_storage import (
    ObjectMetadata,
    StorageError,
)

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
TENANT = "acme"
PROJECT = "adas"

_AUDIT: dict[str, Any] = {
    "tenant_id": TENANT,
    "created_at": NOW,
    "created_by": "o.mathieu",
    "updated_at": NOW,
    "updated_by": "o.mathieu",
}

_SCOPE = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)


class FakeStorage:
    """In-memory stand-in for the MinIO facade (dependency double)."""

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
        del self.blobs[path]

    async def list_objects(self, prefix: str) -> list[ObjectMetadata]:
        return [
            ObjectMetadata(path=p, size=len(d), etag="")
            for p, d in sorted(self.blobs.items())
            if p.startswith(prefix)
        ]


@pytest.fixture
def store() -> tuple[ProcessStorage, FakeStorage]:
    fake = FakeStorage()
    return ProcessStorage(fake), fake  # type: ignore[arg-type]


def _cycle(**overrides: Any) -> CycleDefinition:
    payload: dict[str, Any] = {
        **_AUDIT,
        "cycle_id": "C-AUTOMOTIVE",
        "title": "Automotive systems engineering",
        "version": 1,
        "containers": (
            ContainerSpec(
                slug="030-ARCHITECTURE-DESIGN",
                ordinal=30,
                scope_id="SC-002",
                scope=_SCOPE,
            ),
        ),
    }
    payload.update(overrides)
    return CycleDefinition.model_validate(payload)


def _workflow(**overrides: Any) -> WorkflowDefinition:
    payload: dict[str, Any] = {
        **_AUDIT,
        "workflow_id": "WF-002",
        "version": 1,
        "intent": "Produce a container so every allocated requirement is covered.",
        "steps": (
            WorkflowStep(
                step_id="s1", kind=StepKind.AGENT, role="doc-author",
                action="draft_objects",
            ),
        ),
        "checks": (
            WorkflowCheck(
                check_id="CRIT-COV-001",
                statement="Every allocated requirement has a covering object.",
            ),
        ),
    }
    payload.update(overrides)
    return WorkflowDefinition.model_validate(payload)


@pytest.mark.unit
class TestPaths:
    def test_tenant_scope(self) -> None:
        assert ProcessStorage.scope_prefix(TENANT, None) == "tenants/acme/process/"

    def test_project_scope(self) -> None:
        assert ProcessStorage.scope_prefix(TENANT, PROJECT) == "projects/adas/process/"

    def test_cycle_path(self) -> None:
        assert ProcessStorage.cycle_path(TENANT, None, "C-AUTOMOTIVE", 4) == (
            "tenants/acme/process/cycles/C-AUTOMOTIVE/v4.json"
        )

    def test_workflow_path(self) -> None:
        assert ProcessStorage.workflow_path(TENANT, PROJECT, "WF-002", 3) == (
            "projects/adas/process/workflows/WF-002/v3.json"
        )

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".hidden"])
    def test_traversal_rejected(self, bad: str) -> None:
        with pytest.raises(ProcessPathError):
            ProcessStorage.scope_prefix(bad, None)

    def test_malformed_ids_rejected(self) -> None:
        with pytest.raises(ProcessPathError, match="Invalid cycle id"):
            ProcessStorage.cycle_path(TENANT, None, "automotive", 1)
        with pytest.raises(ProcessPathError, match="Invalid workflow id"):
            ProcessStorage.workflow_path(TENANT, None, "WF-2", 1)

    def test_version_zero_rejected(self) -> None:
        with pytest.raises(ProcessPathError, match="SHALL be >= 1"):
            ProcessStorage.cycle_path(TENANT, None, "C-AUTOMOTIVE", 0)


@pytest.mark.unit
class TestImmutabilityOfPublishedVersions:
    """R-310-023 / R-310-045 — enforced by the shape of the API."""

    @pytest.mark.asyncio
    async def test_seal_then_read(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.seal(_cycle(status=EntityStatus.APPROVED))
        read = await s.get_cycle(TENANT, None, "C-AUTOMOTIVE", 1)
        assert read.status is EntityStatus.APPROVED
        assert read.title == "Automotive systems engineering"

    @pytest.mark.asyncio
    async def test_seal_promotes_its_own_draft_in_place(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        """A version slot holds the draft, then the sealed definition.

        Promotion in place is the normal lifecycle — the first version of
        this module refused it, because `seal` rejected any occupant rather
        than an already-APPROVED one, and no unit test covered the path.
        """
        s, _ = store
        await s.put_draft(_cycle(title="being written"))
        await s.seal(_cycle(status=EntityStatus.APPROVED, title="published"))
        sealed = await s.get_cycle(TENANT, None, "C-AUTOMOTIVE", 1)
        assert sealed.status is EntityStatus.APPROVED
        assert sealed.title == "published"

    @pytest.mark.asyncio
    async def test_a_scope_mentioning_approved_does_not_defeat_the_guard(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        """The guard parses the stored status; it does not grep for a word."""
        prose = (
            "Container for approved supplier interface agreements and the "
            "approved variant matrix."
        )
        s, _ = store
        await s.put_draft(
            _cycle(
                containers=(
                    ContainerSpec(
                        slug="040-SUPPLIER", ordinal=40, scope_id="SC-004",
                        scope=prose,
                    ),
                )
            )
        )
        # Still a draft, so it remains overwritable despite the prose.
        await s.put_draft(_cycle(title="second write"))
        assert (await s.get_cycle(TENANT, None, "C-AUTOMOTIVE", 1)).title == (
            "second write"
        )

    @pytest.mark.asyncio
    async def test_sealing_twice_is_refused(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.seal(_cycle(status=EntityStatus.APPROVED))
        with pytest.raises(AlreadyPublishedError, match="immutable"):
            await s.seal(_cycle(status=EntityStatus.APPROVED, title="rewritten"))

    @pytest.mark.asyncio
    async def test_a_draft_cannot_overwrite_a_sealed_version(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.seal(_cycle(status=EntityStatus.APPROVED))
        with pytest.raises(AlreadyPublishedError, match="published version"):
            await s.put_draft(_cycle(status=EntityStatus.DRAFT))

    @pytest.mark.asyncio
    async def test_seal_refuses_a_draft(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        with pytest.raises(AlreadyPublishedError, match="approved versions only"):
            await s.seal(_cycle(status=EntityStatus.DRAFT))

    @pytest.mark.asyncio
    async def test_put_draft_refuses_an_approved_definition(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        with pytest.raises(AlreadyPublishedError, match="drafts only"):
            await s.put_draft(_cycle(status=EntityStatus.APPROVED))

    def test_no_api_can_rewrite_a_sealed_version(self) -> None:
        # The absence IS the enforcement (same discipline as DV-08). An API
        # able to rewrite published history would make the
        # `produced_by: WF-nnn@vN` stamp on every object meaningless.
        forbidden = {
            name
            for name in dir(ProcessStorage)
            if any(v in name.lower() for v in ("delete", "remove", "replace", "unseal"))
        }
        assert not forbidden, forbidden


@pytest.mark.unit
class TestDrafts:
    @pytest.mark.asyncio
    async def test_draft_is_overwritable(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.put_draft(_cycle(title="first"))
        await s.put_draft(_cycle(title="second"))
        assert (await s.get_cycle(TENANT, None, "C-AUTOMOTIVE", 1)).title == "second"

    @pytest.mark.asyncio
    async def test_draft_is_stored_as_json(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, fake = store
        await s.put_draft(_cycle())
        path = "tenants/acme/process/cycles/C-AUTOMOTIVE/v1.json"
        assert fake.content_types[path] == "application/json"
        assert json.loads(fake.blobs[path])["cycle_id"] == "C-AUTOMOTIVE"

    @pytest.mark.asyncio
    async def test_workflow_draft_round_trip(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        definition = _workflow()
        await s.put_draft(definition)
        assert await s.get_workflow(TENANT, None, "WF-002", 1) == definition

    @pytest.mark.asyncio
    async def test_missing_version_raises(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        with pytest.raises(FileNotFoundError):
            await s.get_cycle(TENANT, None, "C-AUTOMOTIVE", 9)

    @pytest.mark.asyncio
    async def test_corrupt_payload_is_attributed(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, fake = store
        fake.blobs["tenants/acme/process/cycles/C-AUTOMOTIVE/v1.json"] = b"{}"
        with pytest.raises(StorageError, match="Corrupt CycleDefinition"):
            await s.get_cycle(TENANT, None, "C-AUTOMOTIVE", 1)


@pytest.mark.unit
class TestListing:
    @pytest.mark.asyncio
    async def test_versions_ascending(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        for v in (1, 2, 3):
            await s.put_draft(_cycle(version=v))
        assert await s.list_cycle_versions(TENANT, None, "C-AUTOMOTIVE") == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_scopes_do_not_bleed(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.put_draft(_cycle())
        await s.put_draft(
            _cycle(
                project_id=PROJECT,
                tailoring_of="C-AUTOMOTIVE",
                tailoring_rationale="Security analysis is owned by the OEM.",
            )
        )
        assert await s.list_cycle_versions(TENANT, None, "C-AUTOMOTIVE") == [1]
        assert await s.list_cycle_versions(TENANT, PROJECT, "C-AUTOMOTIVE") == [1]

    @pytest.mark.asyncio
    async def test_entity_ids_listed(
        self, store: tuple[ProcessStorage, FakeStorage]
    ) -> None:
        s, _ = store
        await s.put_draft(_cycle())
        await s.put_draft(_cycle(cycle_id="C-ASPICE"))
        await s.put_draft(_workflow())
        assert await s.list_cycle_ids(TENANT, None) == ["C-ASPICE", "C-AUTOMOTIVE"]
        assert await s.list_workflow_ids(TENANT, None) == ["WF-002"]


@pytest.mark.unit
class TestIndexProjection:
    def test_scope_key_distinguishes_tenant_from_project(self) -> None:
        assert scope_key(TENANT, None) == "t:acme"
        assert scope_key(TENANT, PROJECT) == "p:adas"

    def test_index_key_includes_the_version(self) -> None:
        assert index_key(TENANT, None, "C-AUTOMOTIVE", 4) == (
            "t:acme:C-AUTOMOTIVE:v4"
        )

    def test_cycle_row_holds_metadata_not_container_substance(self) -> None:
        # The container scope STATEMENTS are the substance; duplicating them
        # in the index would create a second place they can drift from.
        # (`row["scope"]` is a different thing — the `t:acme` filter key.)
        row = to_cycle_row(_cycle())
        assert row["container_slugs"] == ["030-ARCHITECTURE-DESIGN"]
        assert row["container_count"] == 1
        assert "containers" not in row
        assert _SCOPE not in json.dumps(row)

    def test_workflow_row_exposes_the_check_ids(self) -> None:
        # The checks are what make a workflow verifiable (R-310-041), so the
        # index can answer "which workflows assert CRIT-COV-001?".
        row = to_workflow_row(_workflow())
        assert row["check_ids"] == ["CRIT-COV-001"]
        assert row["step_count"] == 1

    def test_row_carries_the_scope_for_filtering(self) -> None:
        row = to_cycle_row(_cycle())
        assert row["scope"] == "t:acme"
        assert row["status"] == "draft"
