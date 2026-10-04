# =============================================================================
# File: test_process_activity_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_process_activity_verified.py
# Description: Integration tests for the phase-binding precondition of
#              R-310-025, against real MinIO + ArangoDB.
#
#              The requirement's rationale is that without this precondition
#              "workflows are applied systematically" degrades to "usually".
#              So the tests assert the four ways it can be unmet, each with
#              its own machine-readable reason — a refusal a user cannot act
#              on is as useless as no refusal at all — and the one way it is
#              met.
#
#              Resolution goes through the project's lens throughout: a
#              project that tailored its authoring activity must run its own
#              workflow version, not the tenant's (R-310-022).
#
# @relation validates:R-310-025
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.process.models import (
    ActivityDenialReason,
    ContainerSpec,
)
from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.router import router as process_router
from ay_platform_core.c5_requirements.process.service import (
    ActivityNotPermittedError,
    ProcessService,
)
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

TENANT = "acme"
PROJECT = "adas"
CYCLE = "C-AUTO"
WF = "WF-002"
ARCH = "030-ARCH"
ADMIN = "t.admin"
OWNER = "o.mathieu"

# ONE literal, not an implicit concatenation: the functional-coverage check
# reads path literals from test sources and sees neither half of a split one.
ACTIVITY_URL = "/api/v1/projects/adas/process/cycles/C-AUTO/containers/030-ARCH/activity"

_HDRS = {
    "X-User-Id": "l.perrin",
    "X-User-Roles": "project_viewer",
    "X-Tenant-Id": "acme",
}

_SCOPE_AD = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)

_STEPS = (
    {"step_id": "s1", "kind": "agent", "role": "doc-author",
     "action": "draft_objects"},
)
_CHECKS = (
    {"check_id": "CRIT-COV-001",
     "statement": "Every allocated requirement has a covering object."},
)


def _container(*, bound: bool) -> ContainerSpec:
    return ContainerSpec.model_validate(
        {
            "slug": ARCH, "ordinal": 30, "scope_id": "SC-002",
            "scope": _SCOPE_AD,
            "workflow_id": WF if bound else None,
        }
    )


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[ProcessService, FastAPI]]:
    db_name = f"c5_act_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ProcessRepository(db)
        repo._ensure_collections_sync()
        service = ProcessService(ProcessStorage(c5_storage), repo)
        app = FastAPI()
        app.include_router(process_router)
        app.state.process_service = service
        yield service, app
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
def service(wired: tuple[ProcessService, FastAPI]) -> ProcessService:
    return wired[0]


@pytest.fixture
async def client(
    wired: tuple[ProcessService, FastAPI],
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=wired[1])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _publish_cycle(
    service: ProcessService, *, bound: bool, project: str | None = None, **extra: Any
) -> None:
    await service.create_cycle_draft(
        TENANT, project, cycle_id=CYCLE, title="Automotive",
        containers=(_container(bound=bound),), actor=ADMIN, **extra,
    )
    version = 1
    await service.publish_cycle(TENANT, project, CYCLE, version, actor=ADMIN)


async def _publish_workflow(
    service: ProcessService, *, project: str | None = None, intent: str = "", **extra: Any
) -> None:
    await service.create_workflow_draft(
        TENANT, project, workflow_id=WF,
        intent=intent or "Produce a container so every requirement is covered.",
        steps=tuple(_STEPS), checks=tuple(_CHECKS), actor=ADMIN, **extra,  # type: ignore[arg-type]
    )
    await service.publish_workflow(TENANT, project, WF, 1, actor=ADMIN)


# ---------------------------------------------------------------------------
# The four ways the precondition is unmet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_published_cycle(service: ProcessService) -> None:
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    assert exc.value.reason is ActivityDenialReason.NO_CYCLE


@pytest.mark.asyncio
async def test_unknown_container(service: ProcessService) -> None:
    await _publish_cycle(service, bound=True)
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, "999-NOPE")
    assert exc.value.reason is ActivityDenialReason.UNKNOWN_CONTAINER


@pytest.mark.asyncio
async def test_container_binds_no_workflow(service: ProcessService) -> None:
    """A cycle may declare a container nobody automates — but then no
    activity starts on it, and the reason says so."""
    await _publish_cycle(service, bound=False)
    await _publish_workflow(service)
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    assert exc.value.reason is ActivityDenialReason.NO_WORKFLOW_BOUND


@pytest.mark.asyncio
async def test_bound_workflow_is_not_published(service: ProcessService) -> None:
    """The binding exists but points at a draft — the commonest real case."""
    await _publish_cycle(service, bound=True)
    await service.create_workflow_draft(
        TENANT, None, workflow_id=WF,
        intent="Produce a container so every requirement is covered.",
        steps=tuple(_STEPS), checks=tuple(_CHECKS), actor=ADMIN,  # type: ignore[arg-type]
    )
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    assert exc.value.reason is ActivityDenialReason.WORKFLOW_NOT_PUBLISHED


# ---------------------------------------------------------------------------
# The one way it is met
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binding_resolves_when_everything_is_published(
    service: ProcessService,
) -> None:
    await _publish_cycle(service, bound=True)
    await _publish_workflow(service)

    binding = await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)

    assert binding.container == ARCH
    assert binding.cycle_id == CYCLE
    assert binding.cycle_version == 1
    assert binding.workflow_id == WF
    assert binding.workflow_version == 1
    # The resolved workflow travels WITH the binding: a runner must not have
    # to fetch it again and risk resolving a different version.
    assert [c.check_id for c in binding.workflow.checks] == ["CRIT-COV-001"]


@pytest.mark.asyncio
async def test_a_project_tailored_workflow_is_what_runs(
    service: ProcessService,
) -> None:
    """R-310-022 — resolution goes through the project's lens throughout."""
    await _publish_cycle(service, bound=True)
    await _publish_workflow(service, intent="Tenant standard authoring activity.")
    await _publish_workflow(
        service, project=PROJECT,
        intent="Project activity with the OEM security gate before drafting.",
        tailoring_of=WF,
        tailoring_rationale="The OEM requires a security gate before drafting.",
    )

    binding = await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    assert binding.workflow.project_id == PROJECT
    assert "OEM security gate" in binding.workflow.intent


@pytest.mark.asyncio
async def test_a_project_tailored_cycle_supplies_the_binding(
    service: ProcessService,
) -> None:
    # The tenant cycle leaves the container unbound; the project's tailoring
    # binds it. The activity must follow the project's process.
    await _publish_cycle(service, bound=False)
    await _publish_cycle(
        service, bound=True, project=PROJECT,
        tailoring_of=CYCLE,
        tailoring_rationale="This project automates the architecture design.",
    )
    await _publish_workflow(service)

    binding = await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    assert binding.workflow_id == WF


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_returns_the_binding(
    service: ProcessService, client: httpx.AsyncClient
) -> None:
    await _publish_cycle(service, bound=True)
    await _publish_workflow(service)

    response = await client.get(ACTIVITY_URL, headers=_HDRS)
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"] == WF
    assert body["cycle_version"] == 1


@pytest.mark.asyncio
async def test_http_refusal_carries_an_actionable_reason(
    service: ProcessService, client: httpx.AsyncClient
) -> None:
    """A refusal a user cannot act on is as useless as no refusal."""
    await _publish_cycle(service, bound=True)

    response = await client.get(ACTIVITY_URL, headers=_HDRS)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "workflow-not-published"
    assert WF in detail["message"]


@pytest.mark.asyncio
async def test_http_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get(ACTIVITY_URL)).status_code == 401


@pytest.mark.asyncio
async def test_every_denial_reason_is_reachable(service: ProcessService) -> None:
    # A reason code nothing can produce is dead vocabulary; this pins the
    # enum to behaviour rather than to intent.
    reached: set[ActivityDenialReason] = set()

    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    reached.add(exc.value.reason)

    await _publish_cycle(service, bound=False)
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, "999-NOPE")
    reached.add(exc.value.reason)
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    reached.add(exc.value.reason)

    await service.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive v2",
        containers=(_container(bound=True),), actor=ADMIN,
    )
    await service.publish_cycle(TENANT, None, CYCLE, 2, actor=ADMIN)
    with pytest.raises(ActivityNotPermittedError) as exc:
        await service.resolve_activity(TENANT, PROJECT, CYCLE, ARCH)
    reached.add(exc.value.reason)

    assert reached == set(ActivityDenialReason)
