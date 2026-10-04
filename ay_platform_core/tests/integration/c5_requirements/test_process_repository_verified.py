# =============================================================================
# File: test_process_repository_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_process_repository_verified.py
# Description: Integration tests for ProcessRepository against a real
#              ArangoDB. The index answers one question MinIO answers badly —
#              "which version is approved, at this scope?" — and that answer
#              is pure AQL, so it is only meaningful executed.
#
#              Two properties this tier establishes:
#                - publishing a new version supersedes the previous approved
#                  one in the INDEX while the sealed document keeps the
#                  status it was published with (R-310-023);
#                - a tenant-level catalogue and a project-level tailoring of
#                  the same entity id never see each other.
#
# @relation validates:R-310-020
# @relation validates:R-310-022
# @relation validates:R-310-040
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

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
    COLL_CYCLES,
    COLL_WORKFLOWS,
    ProcessRepository,
)
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

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


def _cycle(**overrides: Any) -> CycleDefinition:
    payload: dict[str, Any] = {
        **_AUDIT,
        "cycle_id": "C-AUTOMOTIVE",
        "title": "Automotive systems engineering",
        "version": 1,
        "containers": (
            ContainerSpec(
                slug="030-ARCHITECTURE-DESIGN", ordinal=30,
                scope_id="SC-002", scope=_SCOPE,
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


@pytest.fixture
def repo(arango_container: ArangoEndpoint) -> Iterator[ProcessRepository]:
    db_name = f"c5_proc_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        r = ProcessRepository(db)
        r._ensure_collections_sync()
        yield r
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(repo: ProcessRepository) -> None:
    await repo.ensure_collections()
    await repo.ensure_collections()
    names = {c["name"] for c in repo._db.collections()}
    assert COLL_CYCLES in names
    assert COLL_WORKFLOWS in names


@pytest.mark.asyncio
async def test_declared_index_exists(repo: ProcessRepository) -> None:
    for name in (COLL_CYCLES, COLL_WORKFLOWS):
        fields = [tuple(i["fields"]) for i in repo._db.collection(name).indexes()]
        assert ("scope", "entity_id", "status") in fields, name


@pytest.mark.asyncio
async def test_next_version_starts_at_one(repo: ProcessRepository) -> None:
    got = await repo.next_version(TENANT, None, "C-AUTOMOTIVE", is_cycle=True)
    assert got == 1


@pytest.mark.asyncio
async def test_next_version_follows_the_highest_stored(
    repo: ProcessRepository,
) -> None:
    # Derived from the index rather than tracked as a counter: a counter is a
    # second source of truth able to drift from what is actually stored.
    for v in (1, 2, 5):
        await repo.upsert_cycle(_cycle(version=v))
    assert await repo.next_version(TENANT, None, "C-AUTOMOTIVE", is_cycle=True) == 6


@pytest.mark.asyncio
async def test_approved_version_is_none_while_only_drafts_exist(
    repo: ProcessRepository,
) -> None:
    await repo.upsert_cycle(_cycle(version=1))
    assert await repo.approved_version(
        TENANT, None, "C-AUTOMOTIVE", is_cycle=True
    ) is None


@pytest.mark.asyncio
async def test_approved_version_is_found(repo: ProcessRepository) -> None:
    await repo.upsert_cycle(_cycle(version=1, status=EntityStatus.APPROVED))
    row = await repo.approved_version(TENANT, None, "C-AUTOMOTIVE", is_cycle=True)
    assert row is not None
    assert row["version"] == 1
    assert row["status"] == "approved"


@pytest.mark.asyncio
async def test_publishing_supersedes_the_previous_approved_version(
    repo: ProcessRepository,
) -> None:
    """R-310-023 — 'superseded' is an index statement, not a rewrite."""
    await repo.upsert_cycle(_cycle(version=1, status=EntityStatus.APPROVED))
    superseded = await repo.supersede_approved(
        TENANT, None, "C-AUTOMOTIVE", is_cycle=True
    )
    await repo.upsert_cycle(_cycle(version=2, status=EntityStatus.APPROVED))

    assert superseded == 1
    current = await repo.approved_version(TENANT, None, "C-AUTOMOTIVE", is_cycle=True)
    assert current is not None
    assert current["version"] == 2

    old = await repo.get_version(TENANT, None, "C-AUTOMOTIVE", 1, is_cycle=True)
    assert old is not None
    assert old["status"] == "superseded"


@pytest.mark.asyncio
async def test_superseding_nothing_reports_zero(repo: ProcessRepository) -> None:
    await repo.upsert_cycle(_cycle(version=1))
    assert await repo.supersede_approved(
        TENANT, None, "C-AUTOMOTIVE", is_cycle=True
    ) == 0


@pytest.mark.asyncio
async def test_tenant_and_project_scopes_are_isolated(
    repo: ProcessRepository,
) -> None:
    """R-310-022 — a project tailoring shares the id but never the scope."""
    await repo.upsert_cycle(_cycle(version=1, status=EntityStatus.APPROVED))
    await repo.upsert_cycle(
        _cycle(
            version=1,
            status=EntityStatus.APPROVED,
            project_id=PROJECT,
            tailoring_of="C-AUTOMOTIVE",
            tailoring_rationale="Security analysis is owned by the OEM.",
        )
    )

    tenant_row = await repo.approved_version(
        TENANT, None, "C-AUTOMOTIVE", is_cycle=True
    )
    project_row = await repo.approved_version(
        TENANT, PROJECT, "C-AUTOMOTIVE", is_cycle=True
    )
    assert tenant_row is not None
    assert project_row is not None
    assert tenant_row["scope"] == "t:acme"
    assert project_row["scope"] == "p:adas"
    assert project_row["tailoring_of"] == "C-AUTOMOTIVE"


@pytest.mark.asyncio
async def test_superseding_one_scope_leaves_the_other_alone(
    repo: ProcessRepository,
) -> None:
    await repo.upsert_cycle(_cycle(version=1, status=EntityStatus.APPROVED))
    await repo.upsert_cycle(
        _cycle(
            version=1,
            status=EntityStatus.APPROVED,
            project_id=PROJECT,
            tailoring_of="C-AUTOMOTIVE",
            tailoring_rationale="Security analysis is owned by the OEM.",
        )
    )

    await repo.supersede_approved(TENANT, None, "C-AUTOMOTIVE", is_cycle=True)

    assert await repo.approved_version(
        TENANT, None, "C-AUTOMOTIVE", is_cycle=True
    ) is None
    assert await repo.approved_version(
        TENANT, PROJECT, "C-AUTOMOTIVE", is_cycle=True
    ) is not None


@pytest.mark.asyncio
async def test_cycles_and_workflows_live_in_separate_collections(
    repo: ProcessRepository,
) -> None:
    await repo.upsert_cycle(_cycle(status=EntityStatus.APPROVED))
    await repo.upsert_workflow(_workflow(status=EntityStatus.APPROVED))

    assert await repo.approved_version(
        TENANT, None, "WF-002", is_cycle=True
    ) is None
    assert await repo.approved_version(
        TENANT, None, "WF-002", is_cycle=False
    ) is not None


@pytest.mark.asyncio
async def test_list_versions_is_ordered(repo: ProcessRepository) -> None:
    for v in (3, 1, 2):
        await repo.upsert_workflow(_workflow(version=v))
    rows = await repo.list_versions(TENANT, None, "WF-002", is_cycle=False)
    assert [r["version"] for r in rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_list_versions_across_entities(repo: ProcessRepository) -> None:
    await repo.upsert_cycle(_cycle(cycle_id="C-ASPICE"))
    await repo.upsert_cycle(_cycle())
    rows = await repo.list_versions(TENANT, None, is_cycle=True)
    assert [r["entity_id"] for r in rows] == ["C-ASPICE", "C-AUTOMOTIVE"]


@pytest.mark.asyncio
async def test_workflow_row_exposes_check_ids(repo: ProcessRepository) -> None:
    await repo.upsert_workflow(_workflow())
    row = await repo.get_version(TENANT, None, "WF-002", 1, is_cycle=False)
    assert row is not None
    assert row["check_ids"] == ["CRIT-COV-001"]


@pytest.mark.asyncio
async def test_unknown_version_returns_none(repo: ProcessRepository) -> None:
    assert await repo.get_version(
        TENANT, None, "C-AUTOMOTIVE", 9, is_cycle=True
    ) is None
