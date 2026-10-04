# =============================================================================
# File: test_process_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_process_service_verified.py
# Description: Integration tests for ProcessService against real MinIO +
#              ArangoDB. This is where the two behaviours increment 2 exists
#              for are demonstrated end to end:
#
#                - editing a published version is impossible; you publish a
#                  NEW version, and the old one stays readable exactly as it
#                  was published (R-310-023, R-310-045);
#                - `resolve_*` answers "what applies to THIS project?" — the
#                  project's tailoring when it has one, the tenant catalogue
#                  otherwise (R-310-022, R-310-046).
#
# @relation validates:R-310-022
# @relation validates:R-310-023
# @relation validates:R-310-041
# @relation validates:R-310-045
# @relation validates:R-310-046
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from pydantic import ValidationError

from ay_platform_core.c5_requirements.process.models import (
    ContainerSpec,
    EntityStatus,
    StepKind,
    WorkflowCheck,
    WorkflowStep,
)
from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.service import (
    NotADraftError,
    ProcessNotFoundError,
    ProcessService,
)
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

TENANT = "acme"
PROJECT = "adas"
CYCLE = "C-AUTOMOTIVE"
WF = "WF-002"
ADMIN = "tenant.admin"
OWNER = "o.mathieu"

_SCOPE_AD = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)
_SCOPE_SEC = (
    "Threat analysis and risk assessment, attack paths, and the security "
    "controls allocated against them."
)


def _containers(*extra: ContainerSpec) -> tuple[ContainerSpec, ...]:
    base = ContainerSpec(
        slug="030-ARCHITECTURE-DESIGN", ordinal=30, scope_id="SC-002",
        scope=_SCOPE_AD,
    )
    return (base, *extra)


def _security_container() -> ContainerSpec:
    return ContainerSpec(
        slug="070-SECURITY-ANALYSIS", ordinal=70, scope_id="SC-003",
        scope=_SCOPE_SEC,
    )


def _steps() -> tuple[WorkflowStep, ...]:
    return (
        WorkflowStep(
            step_id="s1", kind=StepKind.AGENT, role="doc-author",
            action="draft_objects",
        ),
        WorkflowStep(step_id="s2", kind=StepKind.HUMAN_GATE, on_reject="s1"),
    )


def _checks() -> tuple[WorkflowCheck, ...]:
    return (
        WorkflowCheck(
            check_id="CRIT-COV-001",
            statement="Every allocated requirement has at least one covering object.",
        ),
    )


@pytest.fixture
def service(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[ProcessService]:
    db_name = f"c5_psvc_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ProcessRepository(db)
        repo._ensure_collections_sync()
        yield ProcessService(ProcessStorage(c5_storage), repo)
    finally:
        cleanup_arango_database(arango_container, db_name)


# ---------------------------------------------------------------------------
# Draft → publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_starts_at_v1_and_is_not_approved(
    service: ProcessService,
) -> None:
    draft = await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=_containers(), actor=ADMIN,
    )
    assert draft.version == 1
    assert draft.status is EntityStatus.DRAFT
    assert await service.approved_cycle(TENANT, None, CYCLE) is None


@pytest.mark.asyncio
async def test_publishing_makes_it_the_approved_version(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=_containers(), actor=ADMIN,
    )
    published = await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)

    assert published.status is EntityStatus.APPROVED
    current = await service.approved_cycle(TENANT, None, CYCLE)
    assert current is not None
    assert current.version == 1


@pytest.mark.asyncio
async def test_a_published_version_cannot_be_edited(
    service: ProcessService,
) -> None:
    """R-310-023 — the whole point of the lifecycle."""
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=_containers(), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)

    with pytest.raises(NotADraftError, match="immutable"):
        await service.update_cycle_draft(
            TENANT, None, CYCLE, 1, title="rewritten",
            containers=_containers(), actor=ADMIN,
        )


@pytest.mark.asyncio
async def test_a_published_version_cannot_be_republished(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=_containers(), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)
    with pytest.raises(NotADraftError):
        await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)


@pytest.mark.asyncio
async def test_editing_a_published_cycle_means_a_new_version(
    service: ProcessService,
) -> None:
    """The prescribed path, and the old version survives untouched."""
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive v1",
        containers=_containers(), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)

    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive v2",
        containers=_containers(_security_container()), actor=ADMIN,
    )
    v2 = await service.publish_cycle(TENANT, None, CYCLE, 2, actor=ADMIN)

    assert v2.version == 2
    current = await service.approved_cycle(TENANT, None, CYCLE)
    assert current is not None
    assert current.version == 2
    assert len(current.containers) == 2

    # v1 is still readable exactly as it was published.
    v1 = await service.get_cycle(TENANT, None, CYCLE, 1)
    assert v1.title == "Automotive v1"
    assert len(v1.containers) == 1
    assert v1.status is EntityStatus.APPROVED


@pytest.mark.asyncio
async def test_drafts_are_editable_until_published(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="first",
        containers=_containers(), actor=ADMIN,
    )
    updated = await service.update_cycle_draft(
        TENANT, None, CYCLE, 1, title="second",
        containers=_containers(_security_container()), actor=OWNER,
    )
    assert updated.title == "second"
    assert updated.updated_by == OWNER
    assert len(updated.containers) == 2


@pytest.mark.asyncio
async def test_draft_update_is_revalidated(service: ProcessService) -> None:
    # model_copy bypasses validators, so the service re-validates; a caller
    # must not be able to install an empty container list this way.
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="first",
        containers=_containers(), actor=ADMIN,
    )
    with pytest.raises(ValidationError, match="at least one container"):
        await service.update_cycle_draft(
            TENANT, None, CYCLE, 1, title="broken", containers=(), actor=ADMIN,
        )


@pytest.mark.asyncio
async def test_unknown_version_raises_not_found(service: ProcessService) -> None:
    with pytest.raises(ProcessNotFoundError):
        await service.get_cycle(TENANT, None, CYCLE, 9)


# ---------------------------------------------------------------------------
# Workflows and the publication gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_workflow_without_checks_cannot_be_published(
    service: ProcessService,
) -> None:
    """R-310-041 — the line between an activity and a named prompt."""
    await service.create_workflow_draft(
        TENANT, None, workflow_id=WF,
        intent="Produce a container so every allocated requirement is covered.",
        steps=_steps(), checks=(), actor=ADMIN,
    )
    with pytest.raises(ValidationError, match="no checks SHALL NOT be approved"):
        await service.publish_workflow(TENANT, None, WF, 1, actor=ADMIN)

    # And it stays unpublished — the refusal is not cosmetic.
    assert await service.approved_workflow(TENANT, None, WF) is None


@pytest.mark.asyncio
async def test_adding_checks_unblocks_publication(
    service: ProcessService,
) -> None:
    await service.create_workflow_draft(
        TENANT, None, workflow_id=WF,
        intent="Produce a container so every allocated requirement is covered.",
        steps=_steps(), checks=(), actor=ADMIN,
    )
    await service.update_workflow_draft(
        TENANT, None, WF, 1, actor=ADMIN, checks=_checks()
    )
    published = await service.publish_workflow(TENANT, None, WF, 1, actor=ADMIN)
    assert published.status is EntityStatus.APPROVED
    assert [c.check_id for c in published.checks] == ["CRIT-COV-001"]


@pytest.mark.asyncio
async def test_workflow_and_cycle_version_counters_are_independent(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=_containers(), actor=ADMIN,
    )
    wf = await service.create_workflow_draft(
        TENANT, None, workflow_id=WF, intent="Author a container, fully covered.",
        steps=_steps(), checks=_checks(), actor=ADMIN,
    )
    assert wf.version == 1


# ---------------------------------------------------------------------------
# Tailoring resolution — R-310-022 / R-310-046
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_project_without_tailoring_gets_the_tenant_catalogue(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant standard",
        containers=_containers(), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)

    resolved = await service.resolve_cycle(TENANT, PROJECT, CYCLE)
    assert resolved is not None
    assert resolved.title == "Tenant standard"
    assert resolved.project_id is None


@pytest.mark.asyncio
async def test_a_project_tailoring_wins(service: ProcessService) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant standard",
        containers=_containers(_security_container()), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)

    await service.create_cycle_draft(
        TENANT, PROJECT, cycle_id=CYCLE, title="ADAS tailoring",
        containers=_containers(), actor=OWNER,
        tailoring_of=CYCLE,
        tailoring_rationale="Security analysis is owned by the OEM, not by us.",
    )
    await service.publish_cycle(TENANT, PROJECT, CYCLE, 1, actor=OWNER)

    resolved = await service.resolve_cycle(TENANT, PROJECT, CYCLE)
    assert resolved is not None
    assert resolved.title == "ADAS tailoring"
    assert resolved.project_id == PROJECT
    assert resolved.tailoring_rationale is not None


@pytest.mark.asyncio
async def test_tailoring_replaces_rather_than_merges(
    service: ProcessService,
) -> None:
    """A merge would produce a definition nobody authored and nobody reviewed."""
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant standard",
        containers=_containers(_security_container()), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)

    await service.create_cycle_draft(
        TENANT, PROJECT, cycle_id=CYCLE, title="ADAS tailoring",
        containers=_containers(), actor=OWNER,
        tailoring_of=CYCLE,
        tailoring_rationale="Security analysis is owned by the OEM, not by us.",
    )
    await service.publish_cycle(TENANT, PROJECT, CYCLE, 1, actor=OWNER)

    resolved = await service.resolve_cycle(TENANT, PROJECT, CYCLE)
    assert resolved is not None
    slugs = [c.slug for c in resolved.containers]
    assert slugs == ["030-ARCHITECTURE-DESIGN"]
    assert "070-SECURITY-ANALYSIS" not in slugs


@pytest.mark.asyncio
async def test_an_unpublished_tailoring_does_not_win(
    service: ProcessService,
) -> None:
    # A draft override must not silently change what a project runs on.
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant standard",
        containers=_containers(), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)
    await service.create_cycle_draft(
        TENANT, PROJECT, cycle_id=CYCLE, title="Work in progress",
        containers=_containers(), actor=OWNER,
        tailoring_of=CYCLE, tailoring_rationale="Still deciding.",
    )

    resolved = await service.resolve_cycle(TENANT, PROJECT, CYCLE)
    assert resolved is not None
    assert resolved.title == "Tenant standard"


@pytest.mark.asyncio
async def test_resolution_returns_none_when_nothing_is_published(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant standard",
        containers=_containers(), actor=ADMIN,
    )
    assert await service.resolve_cycle(TENANT, PROJECT, CYCLE) is None


@pytest.mark.asyncio
async def test_workflow_tailoring_resolves_the_same_way(
    service: ProcessService,
) -> None:
    await service.create_workflow_draft(
        TENANT, None, workflow_id=WF, intent="Tenant standard authoring activity.",
        steps=_steps(), checks=_checks(), actor=ADMIN,
    )
    await service.publish_workflow(TENANT, None, WF, 1, actor=ADMIN)

    assert (await service.resolve_workflow(TENANT, PROJECT, WF)) is not None

    await service.create_workflow_draft(
        TENANT, PROJECT, workflow_id=WF,
        intent="Project activity with an extra security review gate.",
        steps=_steps(), checks=_checks(), actor=OWNER,
        tailoring_of=WF,
        tailoring_rationale="The OEM requires a security gate before drafting.",
    )
    await service.publish_workflow(TENANT, PROJECT, WF, 1, actor=OWNER)

    resolved = await service.resolve_workflow(TENANT, PROJECT, WF)
    assert resolved is not None
    assert resolved.project_id == PROJECT


@pytest.mark.asyncio
async def test_publishing_a_tenant_version_leaves_the_tailoring_alone(
    service: ProcessService,
) -> None:
    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant v1",
        containers=_containers(), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 1, actor=ADMIN)
    await service.create_cycle_draft(
        TENANT, PROJECT, cycle_id=CYCLE, title="ADAS tailoring",
        containers=_containers(), actor=OWNER,
        tailoring_of=CYCLE, tailoring_rationale="OEM owns the security analysis.",
    )
    await service.publish_cycle(TENANT, PROJECT, CYCLE, 1, actor=OWNER)

    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Tenant v2",
        containers=_containers(_security_container()), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 2, actor=ADMIN)

    # The tenant catalogue moved on; the project keeps what it published.
    resolved = await service.resolve_cycle(TENANT, PROJECT, CYCLE)
    assert resolved is not None
    assert resolved.title == "ADAS tailoring"
