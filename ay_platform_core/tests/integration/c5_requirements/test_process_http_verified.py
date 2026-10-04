# =============================================================================
# File: test_process_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_process_http_verified.py
# Description: HTTP-level integration tests for the authoring surface of
#              R-310-047, against real MinIO + ArangoDB.
#
#              The service tier proves the lifecycle; this tier proves the
#              things only the boundary can establish:
#                - `tenant_admin` writes the catalogue, `project_owner`
#                  writes tailorings, and neither can do the other's job
#                  (C-02, no new role introduced);
#                - editing a published version surfaces as 409 with the
#                  remedy in the message (R-310-023);
#                - publishing a workflow with no checks surfaces as 422 —
#                  the publication gate binds on the wire (R-310-041).
#
#              URLs are written as full literals so the functional-coverage
#              coherence check can see which paths are exercised.
#
# @relation validates:R-310-022
# @relation validates:R-310-023
# @relation validates:R-310-041
# @relation validates:R-310-046
# @relation validates:R-310-047
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.router import router as process_router
from ay_platform_core.c5_requirements.process.service import ProcessService
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

T_CYCLES = "/api/v1/process/cycles"
T_CYCLE_V1 = "/api/v1/process/cycles/C-AUTOMOTIVE/versions/1"
T_CYCLE_V1_PUB = "/api/v1/process/cycles/C-AUTOMOTIVE/versions/1/publish"
T_CYCLE_V2_PUB = "/api/v1/process/cycles/C-AUTOMOTIVE/versions/2/publish"
T_WORKFLOWS = "/api/v1/process/workflows"
T_WF_V1 = "/api/v1/process/workflows/WF-002/versions/1"
T_WF_V1_PUB = "/api/v1/process/workflows/WF-002/versions/1/publish"

P_CYCLES = "/api/v1/projects/adas/process/cycles"
P_CYCLE_V1 = "/api/v1/projects/adas/process/cycles/C-AUTOMOTIVE/versions/1"
P_CYCLE_V1_PUB = "/api/v1/projects/adas/process/cycles/C-AUTOMOTIVE/versions/1/publish"
P_CYCLE_RESOLVED = "/api/v1/projects/adas/process/cycles/C-AUTOMOTIVE/resolved"
P_WORKFLOWS = "/api/v1/projects/adas/process/workflows"
P_WF_V1 = "/api/v1/projects/adas/process/workflows/WF-002/versions/1"
P_WF_V1_PUB = "/api/v1/projects/adas/process/workflows/WF-002/versions/1/publish"
P_WF_RESOLVED = "/api/v1/projects/adas/process/workflows/WF-002/resolved"

_TENANT_HDR = {"X-Tenant-Id": "acme"}
_ADMIN = {"X-User-Id": "t.admin", "X-User-Roles": "tenant_admin", **_TENANT_HDR}
_OWNER = {"X-User-Id": "o.mathieu", "X-User-Roles": "project_owner", **_TENANT_HDR}
_EDITOR = {"X-User-Id": "m.roche", "X-User-Roles": "project_editor", **_TENANT_HDR}
_VIEWER = {"X-User-Id": "l.perrin", "X-User-Roles": "project_viewer", **_TENANT_HDR}

_SCOPE_AD = (
    "Component partitioning, allocation of behaviour to components, "
    "redundancy and independence arguments."
)
_SCOPE_SEC = (
    "Threat analysis and risk assessment, attack paths, and the security "
    "controls allocated against them."
)


def _cycle_body(
    title: str = "Automotive", *, with_security: bool = False, **extra: Any
) -> dict[str, Any]:
    containers: list[dict[str, Any]] = [
        {
            "slug": "030-ARCHITECTURE-DESIGN", "ordinal": 30,
            "scope_id": "SC-002", "scope": _SCOPE_AD,
        }
    ]
    if with_security:
        containers.append(
            {
                "slug": "070-SECURITY-ANALYSIS", "ordinal": 70,
                "scope_id": "SC-003", "scope": _SCOPE_SEC,
            }
        )
    return {"cycle_id": "C-AUTOMOTIVE", "title": title, "containers": containers, **extra}


def _wf_body(*, with_checks: bool = True, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workflow_id": "WF-002",
        "intent": "Produce a container so every allocated requirement is covered.",
        "steps": [
            {"step_id": "s1", "kind": "agent", "role": "doc-author",
             "action": "draft_objects"},
        ],
        "checks": (
            [{"check_id": "CRIT-COV-001",
              "statement": "Every allocated requirement has a covering object."}]
            if with_checks
            else []
        ),
    }
    body.update(extra)
    return body


@pytest.fixture
def process_app(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[FastAPI]:
    db_name = f"c5_phttp_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ProcessRepository(db)
        repo._ensure_collections_sync()
        app = FastAPI()
        app.include_router(process_router)
        app.state.process_service = ProcessService(ProcessStorage(c5_storage), repo)
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
async def client(process_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=process_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get(T_CYCLES)).status_code == 401
    assert (await client.post(T_CYCLES, json=_cycle_body())).status_code == 401


@pytest.mark.asyncio
async def test_missing_tenant_header_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.get(T_CYCLES, headers={"X-User-Id": "someone"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_only_tenant_admin_writes_the_catalogue(
    client: httpx.AsyncClient,
) -> None:
    """C-02 — the catalogue is tenant methodology, not project content."""
    assert (
        await client.post(T_CYCLES, json=_cycle_body(), headers=_OWNER)
    ).status_code == 403
    assert (
        await client.post(T_CYCLES, json=_cycle_body(), headers=_EDITOR)
    ).status_code == 403
    assert (
        await client.post(T_CYCLES, json=_cycle_body(), headers=_ADMIN)
    ).status_code == 201


@pytest.mark.asyncio
async def test_only_project_owner_writes_a_tailoring(
    client: httpx.AsyncClient,
) -> None:
    body = _cycle_body(
        "ADAS", tailoring_of="C-AUTOMOTIVE",
        tailoring_rationale="Security analysis is owned by the OEM, not by us.",
    )
    assert (await client.post(P_CYCLES, json=body, headers=_EDITOR)).status_code == 403
    assert (await client.post(P_CYCLES, json=body, headers=_ADMIN)).status_code == 403
    assert (await client.post(P_CYCLES, json=body, headers=_OWNER)).status_code == 201


@pytest.mark.asyncio
async def test_reads_are_open_to_any_member(client: httpx.AsyncClient) -> None:
    # Everyone working under a process needs to see it: the allocation
    # citations of R-310-066 are unverifiable otherwise.
    await client.post(T_CYCLES, json=_cycle_body(), headers=_ADMIN)
    assert (await client.get(T_CYCLES, headers=_VIEWER)).status_code == 200
    assert (await client.get(T_CYCLE_V1, headers=_VIEWER)).status_code == 200


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_edit_publish(client: httpx.AsyncClient) -> None:
    created = await client.post(T_CYCLES, json=_cycle_body("v1 draft"), headers=_ADMIN)
    assert created.status_code == 201
    assert created.json()["version"] == 1
    assert created.json()["status"] == "draft"

    edited = await client.put(
        T_CYCLE_V1, json={"title": "edited", "containers": _cycle_body()["containers"]},
        headers=_ADMIN,
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "edited"

    published = await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)
    assert published.status_code == 200
    assert published.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_editing_a_published_version_is_409_with_the_remedy(
    client: httpx.AsyncClient,
) -> None:
    """R-310-023 — and the message names what to do instead."""
    await client.post(T_CYCLES, json=_cycle_body(), headers=_ADMIN)
    await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)

    refused = await client.put(
        T_CYCLE_V1,
        json={"title": "rewritten", "containers": _cycle_body()["containers"]},
        headers=_ADMIN,
    )
    assert refused.status_code == 409
    assert "create a new draft version" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_a_new_version_is_the_prescribed_path(
    client: httpx.AsyncClient,
) -> None:
    await client.post(T_CYCLES, json=_cycle_body("v1"), headers=_ADMIN)
    await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)

    second = await client.post(
        T_CYCLES, json=_cycle_body("v2", with_security=True), headers=_ADMIN
    )
    assert second.json()["version"] == 2
    assert (await client.post(T_CYCLE_V2_PUB, headers=_ADMIN)).status_code == 200

    # v1 is still readable exactly as published.
    v1 = await client.get(T_CYCLE_V1, headers=_VIEWER)
    assert v1.json()["title"] == "v1"
    assert len(v1.json()["containers"]) == 1


@pytest.mark.asyncio
async def test_republishing_is_409(client: httpx.AsyncClient) -> None:
    await client.post(T_CYCLES, json=_cycle_body(), headers=_ADMIN)
    await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)
    assert (await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)).status_code == 409


@pytest.mark.asyncio
async def test_unknown_version_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(T_CYCLE_V1, headers=_VIEWER)).status_code == 404


@pytest.mark.asyncio
async def test_listing_reports_versions_and_status(
    client: httpx.AsyncClient,
) -> None:
    await client.post(T_CYCLES, json=_cycle_body(), headers=_ADMIN)
    await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)
    listing = await client.get(T_CYCLES, headers=_VIEWER)
    rows = listing.json()["versions"]
    assert [(r["entity_id"], r["version"], r["status"]) for r in rows] == [
        ("C-AUTOMOTIVE", 1, "approved")
    ]


@pytest.mark.asyncio
async def test_a_malformed_cycle_id_is_422(client: httpx.AsyncClient) -> None:
    body = _cycle_body()
    body["cycle_id"] = "automotive"
    assert (await client.post(T_CYCLES, json=body, headers=_ADMIN)).status_code == 422


@pytest.mark.asyncio
async def test_a_token_container_scope_is_422(client: httpx.AsyncClient) -> None:
    # R-310-066: the scope is what an allocation cites. A token makes the
    # citation meaningless, so it is refused at the boundary.
    body = _cycle_body()
    body["containers"][0]["scope"] = "architecture"
    assert (await client.post(T_CYCLES, json=body, headers=_ADMIN)).status_code == 422


# ---------------------------------------------------------------------------
# The publication gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_workflow_without_checks_cannot_be_published(
    client: httpx.AsyncClient,
) -> None:
    """R-310-041 — the gate binds on the wire, not just in Python."""
    created = await client.post(
        T_WORKFLOWS, json=_wf_body(with_checks=False), headers=_ADMIN
    )
    assert created.status_code == 201

    refused = await client.post(T_WF_V1_PUB, headers=_ADMIN)
    assert refused.status_code == 422
    assert "no checks SHALL NOT be approved" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_adding_checks_unblocks_publication(
    client: httpx.AsyncClient,
) -> None:
    await client.post(T_WORKFLOWS, json=_wf_body(with_checks=False), headers=_ADMIN)
    body = _wf_body()
    body.pop("workflow_id")
    assert (await client.put(T_WF_V1, json=body, headers=_ADMIN)).status_code == 200
    published = await client.post(T_WF_V1_PUB, headers=_ADMIN)
    assert published.status_code == 200
    assert published.json()["checks"][0]["check_id"] == "CRIT-COV-001"


@pytest.mark.asyncio
async def test_a_forward_return_target_is_refused_at_the_boundary(
    client: httpx.AsyncClient,
) -> None:
    """R-310-043 — a loop cannot be smuggled in through JSON."""
    body = _wf_body(
        steps=[
            {"step_id": "s1", "kind": "human-gate", "on_reject": "s2"},
            {"step_id": "s2", "kind": "agent", "role": "r", "action": "a"},
        ]
    )
    assert (await client.post(T_WORKFLOWS, json=body, headers=_ADMIN)).status_code == 422


# ---------------------------------------------------------------------------
# Tailoring and resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolution_falls_back_to_the_tenant_catalogue(
    client: httpx.AsyncClient,
) -> None:
    await client.post(T_CYCLES, json=_cycle_body("Tenant standard"), headers=_ADMIN)
    await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)

    resolved = await client.get(P_CYCLE_RESOLVED, headers=_VIEWER)
    assert resolved.status_code == 200
    assert resolved.json()["title"] == "Tenant standard"
    assert resolved.json()["project_id"] is None


@pytest.mark.asyncio
async def test_a_published_tailoring_wins_resolution(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        T_CYCLES, json=_cycle_body("Tenant standard", with_security=True),
        headers=_ADMIN,
    )
    await client.post(T_CYCLE_V1_PUB, headers=_ADMIN)

    await client.post(
        P_CYCLES,
        json=_cycle_body(
            "ADAS tailoring", tailoring_of="C-AUTOMOTIVE",
            tailoring_rationale="Security analysis is owned by the OEM, not by us.",
        ),
        headers=_OWNER,
    )
    await client.post(P_CYCLE_V1_PUB, headers=_OWNER)

    resolved = await client.get(P_CYCLE_RESOLVED, headers=_VIEWER)
    assert resolved.json()["title"] == "ADAS tailoring"
    # Replacement, not merge: the dropped container does not come back.
    slugs = [c["slug"] for c in resolved.json()["containers"]]
    assert slugs == ["030-ARCHITECTURE-DESIGN"]


@pytest.mark.asyncio
async def test_a_tailoring_without_a_rationale_is_422(
    client: httpx.AsyncClient,
) -> None:
    """R-310-046 — an override with no stated reason cannot be reviewed."""
    response = await client.post(
        P_CYCLES, json=_cycle_body("ADAS", tailoring_of="C-AUTOMOTIVE"),
        headers=_OWNER,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_resolution_is_404_when_nothing_is_published(
    client: httpx.AsyncClient,
) -> None:
    await client.post(T_CYCLES, json=_cycle_body(), headers=_ADMIN)
    missing = await client.get(P_CYCLE_RESOLVED, headers=_VIEWER)
    assert missing.status_code == 404
    assert "neither a project tailoring nor a tenant catalogue" in (
        missing.json()["detail"]
    )


@pytest.mark.asyncio
async def test_workflow_tailoring_and_resolution(
    client: httpx.AsyncClient,
) -> None:
    await client.post(T_WORKFLOWS, json=_wf_body(), headers=_ADMIN)
    await client.post(T_WF_V1_PUB, headers=_ADMIN)
    assert (await client.get(P_WF_RESOLVED, headers=_VIEWER)).status_code == 200

    await client.post(
        P_WORKFLOWS,
        json=_wf_body(
            tailoring_of="WF-002",
            tailoring_rationale="The OEM requires a security gate before drafting.",
        ),
        headers=_OWNER,
    )
    await client.post(P_WF_V1_PUB, headers=_OWNER)

    resolved = await client.get(P_WF_RESOLVED, headers=_VIEWER)
    assert resolved.json()["project_id"] == "adas"


@pytest.mark.asyncio
async def test_project_draft_is_readable_before_publication(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        P_CYCLES,
        json=_cycle_body(
            "WIP", tailoring_of="C-AUTOMOTIVE",
            tailoring_rationale="Deciding whether the OEM owns security.",
        ),
        headers=_OWNER,
    )
    draft = await client.get(P_CYCLE_V1, headers=_OWNER)
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_project_workflow_draft_is_readable(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        P_WORKFLOWS,
        json=_wf_body(
            tailoring_of="WF-002",
            tailoring_rationale="Extra security gate required by the OEM.",
        ),
        headers=_OWNER,
    )
    assert (await client.get(P_WF_V1, headers=_OWNER)).status_code == 200


@pytest.mark.asyncio
async def test_project_workflow_draft_is_editable_then_publishable(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        P_WORKFLOWS,
        json=_wf_body(
            with_checks=False, tailoring_of="WF-002",
            tailoring_rationale="Extra security gate required by the OEM.",
        ),
        headers=_OWNER,
    )
    body = _wf_body()
    body.pop("workflow_id")
    assert (await client.put(P_WF_V1, json=body, headers=_OWNER)).status_code == 200
    assert (await client.post(P_WF_V1_PUB, headers=_OWNER)).status_code == 200


@pytest.mark.asyncio
async def test_tenant_workflow_listing(client: httpx.AsyncClient) -> None:
    await client.post(T_WORKFLOWS, json=_wf_body(), headers=_ADMIN)
    listing = await client.get(T_WORKFLOWS, headers=_VIEWER)
    assert listing.json()["versions"][0]["entity_id"] == "WF-002"


@pytest.mark.asyncio
async def test_tenant_workflow_version_read(client: httpx.AsyncClient) -> None:
    await client.post(T_WORKFLOWS, json=_wf_body(), headers=_ADMIN)
    read = await client.get(T_WF_V1, headers=_VIEWER)
    assert read.status_code == 200
    assert read.json()["workflow_id"] == "WF-002"
