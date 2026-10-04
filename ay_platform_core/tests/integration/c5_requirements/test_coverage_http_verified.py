# =============================================================================
# File: test_coverage_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_coverage_http_verified.py
# Description: HTTP-level integration tests for the traceability surface,
#              against real MinIO + ArangoDB with a really published cycle.
#
#              The service tier proves the rules; this tier proves what only
#              the boundary can:
#                - a process refusal surfaces as 409 with the RULE in the
#                  message, so the workbench can explain instead of failing;
#                - returning an allocation and declaring a requirement
#                  out-of-project are owner-gated, authoring is editor-gated,
#                  and reads are open to any member;
#                - the matrix and the suspect list are actually served.
#
#              URLs are full literals for the functional-coverage check.
#
# @relation validates:R-310-065
# @relation validates:R-310-066
# @relation validates:R-310-068
# @relation validates:R-310-069
# @relation validates:R-310-121
# @relation validates:R-310-145
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.coverage.repository import CoverageRepository
from ay_platform_core.c5_requirements.coverage.router import router as coverage_router
from ay_platform_core.c5_requirements.coverage.service import CoverageService
from ay_platform_core.c5_requirements.process.models import ContainerSpec
from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.service import ProcessService
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

TENANT = "acme"
PID = "adas"
CYCLE = "C-AUTO"
ARCH = "030-ARCH"
SEC = "070-SEC"
REQ = "REQ-SYS-118"

ALLOCATIONS = "/api/v1/projects/adas/coverage/allocations"
ACCEPT = "/api/v1/projects/adas/coverage/allocations/REQ-SYS-118/containers/030-ARCH/accept"
RETURN = "/api/v1/projects/adas/coverage/allocations/REQ-SYS-118/containers/030-ARCH/return"
VERDICTS = "/api/v1/projects/adas/coverage/verdicts"
LINKS = "/api/v1/projects/adas/coverage/links"
REQ_COVERAGE = "/api/v1/projects/adas/coverage/requirements/REQ-SYS-118"
CONTAINER_COVERAGE = "/api/v1/projects/adas/coverage/containers/030-ARCH"
SUSPECT = "/api/v1/projects/adas/coverage/suspect"
AUDIT = "/api/v1/projects/adas/coverage/audit/unallocated"

_TEN = {"X-Tenant-Id": "acme"}
_EDITOR = {"X-User-Id": "agent:allocator", "X-User-Roles": "project_editor", **_TEN}
_OWNER = {"X-User-Id": "o.mathieu", "X-User-Roles": "project_owner", **_TEN}
_VIEWER = {"X-User-Id": "l.perrin", "X-User-Roles": "project_viewer", **_TEN}

_JUST = "Torque allocation is declared by the architecture design container."
_SCOPE_AD = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)
_SCOPE_SEC = (
    "Threat analysis and risk assessment, attack paths, and the security "
    "controls allocated against them."
)


class Versions:
    def __init__(self) -> None:
        self.table: dict[str, int] = {REQ: 4}

    async def __call__(self, project_id: str, target_id: str) -> int | None:
        return self.table.get(target_id)


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[FastAPI, Versions, ProcessService]]:
    db_name = f"c5_covhttp_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        process_repo = ProcessRepository(db)
        process_repo._ensure_collections_sync()
        process = ProcessService(ProcessStorage(c5_storage), process_repo)
        coverage_repo = CoverageRepository(db)
        coverage_repo._ensure_collections_sync()
        versions = Versions()
        app = FastAPI()
        app.include_router(coverage_router)
        app.state.coverage_service = CoverageService(coverage_repo, process, versions)
        yield app, versions, process
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
async def client(
    wired: tuple[FastAPI, Versions, ProcessService],
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=wired[0])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
def versions(wired: tuple[FastAPI, Versions, ProcessService]) -> Versions:
    return wired[1]


@pytest.fixture
async def published_cycle(wired: tuple[FastAPI, Versions, ProcessService]) -> None:
    process = wired[2]
    await process.create_cycle_draft(
        TENANT, None, cycle_id=CYCLE, title="Automotive",
        containers=(
            ContainerSpec(slug=ARCH, ordinal=30, scope_id="SC-002", scope=_SCOPE_AD),
            ContainerSpec(slug=SEC, ordinal=70, scope_id="SC-003", scope=_SCOPE_SEC),
        ),
        actor="t.admin",
    )
    await process.publish_cycle(TENANT, None, CYCLE, 1, actor="t.admin")


def _body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cycle_id": CYCLE, "requirement_id": REQ, "container": ARCH,
        "scope_id": "SC-002", "justification": _JUST,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post(ALLOCATIONS, json=_body())).status_code == 401
    assert (await client.get(REQ_COVERAGE)).status_code == 401


@pytest.mark.asyncio
async def test_authoring_is_editor_gated(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    assert (
        await client.post(ALLOCATIONS, json=_body(), headers=_VIEWER)
    ).status_code == 403
    assert (
        await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    ).status_code == 201


@pytest.mark.asyncio
async def test_returning_and_verdicts_are_owner_gated(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    """R-310-068 reserves the return to the container owner."""
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    refused = await client.post(
        RETURN,
        json={"reason": "out-of-scope", "detail": "Torque limits are technical."},
        headers=_EDITOR,
    )
    assert refused.status_code == 403

    assert (
        await client.post(
            VERDICTS,
            json={"requirement_id": "REQ-9",
                  "justification": "Homologation belongs to the OEM, not to us."},
            headers=_EDITOR,
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_reads_are_open_to_any_member(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    # The matrix is what tells a team what it owes; hiding it from the
    # people doing the work would defeat its purpose.
    assert (await client.get(REQ_COVERAGE, headers=_VIEWER)).status_code == 200
    assert (await client.get(CONTAINER_COVERAGE, headers=_VIEWER)).status_code == 200
    assert (await client.get(SUSPECT, headers=_VIEWER)).status_code == 200


# ---------------------------------------------------------------------------
# Refusals carry the rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wrong_scope_citation_is_409_naming_the_rule(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    """The workbench must be able to explain, not merely fail."""
    response = await client.post(
        ALLOCATIONS, json=_body(scope_id="SC-003"), headers=_EDITOR
    )
    assert response.status_code == 409
    assert "R-310-066" in response.json()["detail"]
    assert "SC-002" in response.json()["detail"]


@pytest.mark.asyncio
async def test_covering_an_unallocated_target_is_409(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    response = await client.post(
        LINKS,
        json={"object_id": "OBJ-1", "container": ARCH, "target_id": REQ},
        headers=_EDITOR,
    )
    assert response.status_code == 409
    assert "R-310-121" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_blank_justification_is_422(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    response = await client.post(
        ALLOCATIONS, json=_body(justification="because"), headers=_EDITOR
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# The loop terminator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_second_return_escalates_over_http(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    """R-310-069 — the response tells the caller to stop trying."""
    body = {"reason": "out-of-scope", "detail": "Torque limits are technical design."}

    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    first = await client.post(RETURN, json=body, headers=_OWNER)
    assert first.json()["ordinal"] == 1
    assert first.json()["escalated"] is False
    assert first.json()["next_exclusions"] == [ARCH]

    blocked = await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    assert blocked.status_code == 409
    assert "R-310-069" in blocked.json()["detail"]

    await client.post(
        ALLOCATIONS, json=_body(override_exclusion=True), headers=_EDITOR
    )
    second = await client.post(RETURN, json=body, headers=_OWNER)
    assert second.json()["ordinal"] == 2
    assert second.json()["escalated"] is True


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_partially_answered_requirement_reports_partial(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    """R-310-064 — two allocations, one answered, is not covered."""
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    await client.post(
        ALLOCATIONS, json=_body(container=SEC, scope_id="SC-003"), headers=_EDITOR
    )
    await client.post(
        LINKS,
        json={"object_id": "OBJ-1", "container": ARCH, "target_id": REQ},
        headers=_EDITOR,
    )

    coverage = (await client.get(REQ_COVERAGE, headers=_VIEWER)).json()
    containers = {a["container"] for a in coverage["allocations"]}
    assert containers == {ARCH, SEC}
    covered = {
        a["container"]: bool(a["covering_objects"]) for a in coverage["allocations"]
    }
    assert covered == {ARCH: True, SEC: False}


@pytest.mark.asyncio
async def test_cluster_acceptance_is_visible_on_the_wire(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    # R-310-007: a coverage figure must be able to say how much was never
    # examined individually, so the state has to reach the client.
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    accepted = await client.post(ACCEPT, json={"auto": True}, headers=_EDITOR)
    assert accepted.json()["state"] == "auto-accepted"


@pytest.mark.asyncio
async def test_the_container_view_reports_what_is_owed(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    container = (await client.get(CONTAINER_COVERAGE, headers=_VIEWER)).json()
    assert container["allocated"] == [REQ]
    assert container["uncovered"] == [REQ]


@pytest.mark.asyncio
async def test_suspect_links_surface_after_the_target_moves(
    client: httpx.AsyncClient, published_cycle: None, versions: Versions
) -> None:
    """R-310-145 — the customer changed the requirement; the link is suspect."""
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    await client.post(
        LINKS,
        json={"object_id": "OBJ-1", "container": ARCH, "target_id": REQ},
        headers=_EDITOR,
    )
    assert (await client.get(SUSPECT, headers=_VIEWER)).json()["links"] == []

    versions.table[REQ] = 5
    suspect = (await client.get(SUSPECT, headers=_VIEWER)).json()["links"]
    assert len(suspect) == 1
    assert suspect[0]["pinned_version"] == 4
    assert suspect[0]["current_version"] == 5


@pytest.mark.asyncio
async def test_the_unallocated_audit_over_http(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    """R-310-065 — asked of the candidate set, which is why it is a POST."""
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    await client.post(
        VERDICTS,
        json={"requirement_id": "REQ-9",
              "justification": "Homologation belongs to the OEM, not to us."},
        headers=_OWNER,
    )
    audit = await client.post(
        AUDIT, json={"candidates": [REQ, "REQ-9", "REQ-77"]}, headers=_VIEWER
    )
    assert audit.json()["unallocated"] == ["REQ-77"]


@pytest.mark.asyncio
async def test_weak_coverage_reaches_the_client_distinctly(
    client: httpx.AsyncClient, published_cycle: None
) -> None:
    await client.post(ALLOCATIONS, json=_body(), headers=_EDITOR)
    await client.post(
        LINKS,
        json={"object_id": "OBJ-1", "container": ARCH, "target_id": REQ,
              "strength": "weak"},
        headers=_EDITOR,
    )
    coverage = (await client.get(REQ_COVERAGE, headers=_VIEWER)).json()
    allocation = coverage["allocations"][0]
    assert allocation["weak_objects"] == ["OBJ-1"]
    assert allocation["covering_objects"] == []
