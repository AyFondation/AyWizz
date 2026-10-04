# =============================================================================
# File: test_absorption_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_absorption_http_verified.py
# Description: The change-absorption REST surface — 310-SPEC §4.8.
#
#              THE CENTRAL TEST HERE IS A NEGATIVE ONE. T-310-009 asserts
#              that no exposed interface permits creating, assigning,
#              prioritising or setting the status of a change ticket. That
#              is a property of the whole router, not of any one route, so
#              it is checked by enumerating the mounted routes rather than
#              by calling endpoints that are supposed not to exist.
#
#              Paths are written as FULL LITERALS rather than built from a
#              shared prefix: the functional-coverage matcher scans test
#              sources for the catalogued path, and implicit string
#              concatenation hides a route from it — which is how a route
#              once passed the catalogue while being tested by nothing.
#
# @relation validates:R-310-140
# @relation validates:R-310-146
# @relation validates:R-310-148
# @relation validates:R-310-149
# @relation validates:R-310-150
# @relation validates:T-310-009
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.absorption.models import (
    DispositionRequest,
    QualifyRequest,
)
from ay_platform_core.c5_requirements.absorption.repository import (
    AbsorptionRepository,
)
from ay_platform_core.c5_requirements.absorption.router import (
    router as absorption_router,
)
from ay_platform_core.c5_requirements.absorption.service import AbsorptionService
from ay_platform_core.c5_requirements.absorption.storage import AbsorptionStorage
from ay_platform_core.c5_requirements.coverage.models import CoverageLink
from ay_platform_core.c5_requirements.coverage.repository import CoverageRepository
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

ABSORB = "/api/v1/projects/adas/absorb/W14"
CHANGES = "/api/v1/projects/adas/changes"
TICKET = "/api/v1/projects/adas/changes/W14/CUST-001"
CLOSURE = "/api/v1/projects/adas/changes/W14/CUST-001/closure"
QUALIFY = "/api/v1/projects/adas/changes/W14/CUST-001/qualify"
ACCEPT = "/api/v1/projects/adas/changes/W14/CUST-001/qualify/accept"
DISPOSITION = "/api/v1/projects/adas/changes/W14/CUST-001/disposition"
CLOSE = "/api/v1/projects/adas/changes/W14/CUST-001/close"
IMPACT = "/api/v1/projects/adas/impact/CUST-001"

_OWNER = {"X-User-Id": "o.mathieu", "X-User-Roles": "project_owner"}
_EDITOR = {"X-User-Id": "a.dev", "X-User-Roles": "project_editor"}
_VIEWER = {"X-User-Id": "v.iewer", "X-User-Roles": "project_viewer"}

_WAS = "The system shall reduce deceleration to zero within 250 ms."
_NOW_TEXT = "The system shall reduce deceleration to zero within 150 ms."
_WHY = "Re-read against the 150 ms bound; this object quotes no timing."
NOW = datetime(2026, 10, 1, tzinfo=UTC)

_DIFF = {
    "previous": [{"requirement_id": "CUST-001", "text": _WAS}],
    "current": [{"requirement_id": "CUST-001", "text": _NOW_TEXT}],
}


class Versions:
    def __init__(self) -> None:
        self.table: dict[str, int] = {}

    async def __call__(self, project_id: str, target_id: str) -> int | None:
        return self.table.get(target_id)


class Coverage:
    def __init__(self) -> None:
        self.covered = True

    async def __call__(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> bool:
        return self.covered


async def _no_split(
    project_id: str, drop_id: str, requirement_id: str
) -> None:
    return None


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[FastAPI, CoverageRepository, Versions, Coverage]]:
    db_name = f"c5_absorb_http_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        coverage_repo = CoverageRepository(db)
        coverage_repo._ensure_collections_sync()
        absorption_repo = AbsorptionRepository(db)
        absorption_repo._ensure_collections_sync()
        versions, coverage = Versions(), Coverage()
        app = FastAPI()
        app.include_router(absorption_router)
        app.state.absorption_service = AbsorptionService(
            AbsorptionStorage(c5_storage),
            absorption_repo,
            coverage_repo,
            versions,
            coverage,
            _no_split,
        )
        yield app, coverage_repo, versions, coverage
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
async def client(
    wired: tuple[FastAPI, CoverageRepository, Versions, Coverage],
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=wired[0])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
def graph(
    wired: tuple[FastAPI, CoverageRepository, Versions, Coverage],
) -> CoverageRepository:
    return wired[1]


@pytest.fixture
def versions(
    wired: tuple[FastAPI, CoverageRepository, Versions, Coverage],
) -> Versions:
    return wired[2]


@pytest.fixture
def coverage(
    wired: tuple[FastAPI, CoverageRepository, Versions, Coverage],
) -> Coverage:
    return wired[3]


async def _link(
    graph: CoverageRepository, object_id: str, target_id: str, pinned: int = 1
) -> None:
    await graph.put_coverage(
        CoverageLink(
            project_id="adas",
            object_id=object_id,
            container="030-ARCH",
            target_id=target_id,
            pinned_version=pinned,
            actor="o.mathieu",
            at=NOW,
        )
    )


async def _absorb(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(ABSORB, json=_DIFF, headers=_OWNER)


async def _confirm(client: httpx.AsyncClient, node_id: str) -> httpx.Response:
    return await client.post(
        DISPOSITION,
        json={
            "node_id": node_id,
            "kind": "confirmed-unchanged",
            "justification": _WHY,
        },
        headers=_OWNER,
    )


# ---------------------------------------------------------------------------
# R-310-149 — what the surface does NOT expose (T-310-009)
# ---------------------------------------------------------------------------


def test_no_route_creates_assigns_or_prioritises_a_ticket() -> None:
    """T-310-009, checked as a property of the whole router.

    A ticket is derived state: it exists because a supplied requirement
    changed, its scope is the traversal result and its closure condition is
    mechanical. A hand-managed work item beside that would be a second,
    divergent source of truth about what remains to be done.
    """
    forbidden = ("assign", "priorit", "status", "owner", "due")
    for route in absorption_router.routes:
        path = getattr(route, "path", "")
        assert not any(word in path.lower() for word in forbidden), path


def test_the_only_ticket_opening_route_takes_a_diff_not_a_ticket() -> None:
    posts = [
        getattr(route, "path", "")
        for route in absorption_router.routes
        if "POST" in getattr(route, "methods", set())
    ]
    opening = [path for path in posts if path.endswith("/absorb/{drop_id}")]
    assert len(opening) == 1
    # And no POST lands on the collection, which is what "create a ticket"
    # would look like.
    assert "/api/v1/projects/{project_id}/changes" not in posts


def test_no_route_can_write_a_status_because_the_model_has_no_status() -> None:
    for body in (QualifyRequest, DispositionRequest):
        assert "status" not in body.model_fields


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post(ABSORB, json=_DIFF)).status_code == 401
    assert (await client.get(CHANGES)).status_code == 401
    assert (await client.get(IMPACT)).status_code == 401


@pytest.mark.asyncio
async def test_absorbing_is_owner_gated(client: httpx.AsyncClient) -> None:
    """It opens review obligations in containers the caller does not own."""
    assert (await client.post(ABSORB, json=_DIFF, headers=_EDITOR)).status_code == 403


@pytest.mark.asyncio
async def test_qualifying_is_editor_gated(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    refused = await client.post(
        QUALIFY,
        json={
            "node_id": "AD-100",
            "qualification": "no-effect",
            "justification": "The clause this object answers was not touched.",
        },
        headers=_VIEWER,
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_accepting_a_qualification_is_owner_gated(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    """The acceptance IS the human gate of R-310-148; an editor cannot pass it."""
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    refused = await client.post(
        ACCEPT, json={"node_id": "AD-100"}, headers=_EDITOR
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_dispositioning_is_owner_gated(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    refused = await client.post(
        DISPOSITION,
        json={
            "node_id": "AD-100",
            "kind": "confirmed-unchanged",
            "justification": _WHY,
        },
        headers=_EDITOR,
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_closing_is_owner_gated(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    assert (await client.post(CLOSE, headers=_EDITOR)).status_code == 403


@pytest.mark.asyncio
async def test_reads_are_merely_authenticated(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    """A team cannot review what it cannot see."""
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    assert (await client.get(CHANGES, headers=_VIEWER)).status_code == 200
    assert (await client.get(TICKET, headers=_VIEWER)).status_code == 200
    assert (await client.get(CLOSURE, headers=_VIEWER)).status_code == 200
    assert (await client.get(IMPACT, headers=_VIEWER)).status_code == 200


# ---------------------------------------------------------------------------
# Absorbing — R-310-140
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_absorbing_a_change_opens_one_ticket(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    response = await _absorb(client)
    assert response.status_code == 201
    assert response.json()["opened"] == ["adas:W14:CUST-001"]


@pytest.mark.asyncio
async def test_absorbing_an_unchanged_drop_opens_nothing(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        ABSORB,
        json={
            "previous": [{"requirement_id": "CUST-001", "text": _WAS}],
            "current": [{"requirement_id": "CUST-001", "text": _WAS}],
        },
        headers=_OWNER,
    )
    assert response.status_code == 201
    assert response.json()["opened"] == []
    assert response.json()["unchanged_count"] == 1


@pytest.mark.asyncio
async def test_a_reflowed_requirement_is_reported_without_a_ticket(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        ABSORB,
        json={
            "previous": [{"requirement_id": "CUST-001", "text": _WAS}],
            "current": [{"requirement_id": "CUST-001", "text": _WAS.replace(" ", "  ")}],
        },
        headers=_OWNER,
    )
    assert response.json()["opened"] == []
    assert response.json()["reformatted_ids"] == ["CUST-001"]


@pytest.mark.asyncio
async def test_a_repeated_identifier_is_refused_with_409(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        ABSORB,
        json={
            "previous": [],
            "current": [
                {"requirement_id": "CUST-001", "text": _WAS},
                {"requirement_id": "CUST-001", "text": _NOW_TEXT},
            ],
        },
        headers=_OWNER,
    )
    assert response.status_code == 409
    assert "R-310-140" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ticket_exposes_its_impact_set_and_derived_status(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "FD-010", "CUST-001")
    await _link(graph, "AD-100", "FD-010")
    await _absorb(client)

    body = (await client.get(TICKET, headers=_VIEWER)).json()
    assert {node["node_id"] for node in body["impact"]["nodes"]} == {"FD-010", "AD-100"}
    # Status is derived, so it is absent from the serialised ticket.
    assert "status" not in body
    assert body["closed_at"] is None


@pytest.mark.asyncio
async def test_listing_can_be_narrowed_to_open_tickets(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    body = (await client.get(CHANGES, params={"only_open": True}, headers=_VIEWER)).json()
    assert body["count"] == 1


@pytest.mark.asyncio
async def test_a_ticket_that_was_never_opened_is_404(
    client: httpx.AsyncClient,
) -> None:
    missing = await client.get(
        "/api/v1/projects/adas/changes/W14/CUST-999", headers=_VIEWER
    )
    assert missing.status_code == 404
    assert "never on request" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_the_impact_preview_reaches_its_own_handler_not_the_ticket_one(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    """Pins the route shapes against shadowing.

    `/impact/{requirement_id}` sits on its own segment precisely so it
    cannot be swallowed by `/changes/{drop_id}/{requirement_id}`. If it
    were, this would 404 with a ticket-not-found instead of returning a set.
    """
    await _link(graph, "AD-100", "CUST-001")
    response = await client.get(IMPACT, headers=_VIEWER)
    assert response.status_code == 200
    assert response.json()["seed_id"] == "CUST-001"
    assert {n["node_id"] for n in response.json()["nodes"]} == {"AD-100"}


# ---------------------------------------------------------------------------
# Qualification, disposition, closure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_modification_before_its_gate_is_refused_with_409(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    await client.post(
        QUALIFY,
        json={
            "node_id": "AD-100",
            "qualification": "substantive-rework",
            "justification": "The stated settling time no longer holds.",
        },
        headers=_EDITOR,
    )
    refused = await client.post(
        DISPOSITION,
        json={
            "node_id": "AD-100",
            "kind": "modified-and-accepted",
            "object_version": 3,
        },
        headers=_OWNER,
    )
    assert refused.status_code == 409
    assert "R-310-148" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_a_modification_after_its_gate_is_accepted(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    await client.post(
        QUALIFY,
        json={
            "node_id": "AD-100",
            "qualification": "substantive-rework",
            "justification": "The stated settling time no longer holds.",
        },
        headers=_EDITOR,
    )
    gated = await client.post(ACCEPT, json={"node_id": "AD-100"}, headers=_OWNER)
    assert gated.status_code == 200
    assert gated.json()["qualifications"][0]["accepted_by"] == "o.mathieu"

    done = await client.post(
        DISPOSITION,
        json={
            "node_id": "AD-100",
            "kind": "modified-and-accepted",
            "object_version": 3,
        },
        headers=_OWNER,
    )
    assert done.status_code == 200
    assert done.json()["dispositions"][0]["object_version"] == 3


@pytest.mark.asyncio
async def test_a_confirmed_unchanged_disposition_without_a_reason_is_422(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    """R-310-150 at the boundary: the model refuses, the API says so."""
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    refused = await client.post(
        DISPOSITION,
        json={"node_id": "AD-100", "kind": "confirmed-unchanged"},
        headers=_OWNER,
    )
    assert refused.status_code == 422


@pytest.mark.asyncio
async def test_a_node_outside_the_impact_set_is_refused_with_409(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    refused = await client.post(
        DISPOSITION,
        json={
            "node_id": "TD-999",
            "kind": "confirmed-unchanged",
            "justification": _WHY,
        },
        headers=_OWNER,
    )
    assert refused.status_code == 409
    assert "R-310-141" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_the_closure_report_names_what_is_outstanding(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _link(graph, "AD-101", "CUST-001")
    await _absorb(client)
    await _confirm(client, "AD-100")

    body = (await client.get(CLOSURE, headers=_VIEWER)).json()
    assert body["node_count"] == 2
    assert body["dispositioned_count"] == 1
    assert body["refusals"][0]["node_id"] == "AD-101"
    assert body["refusals"][0]["blocker"] == "undispositioned-node"


@pytest.mark.asyncio
async def test_closing_is_refused_with_409_carrying_the_whole_report(
    client: httpx.AsyncClient, graph: CoverageRepository, versions: Versions
) -> None:
    """T-310-003: the refusal names which precondition failed."""
    await _link(graph, "AD-100", "CUST-001", pinned=1)
    await _link(graph, "AD-101", "CUST-001", pinned=1)
    await _absorb(client)
    await _confirm(client, "AD-100")
    versions.table["CUST-001"] = 7

    refused = await client.post(CLOSE, headers=_OWNER)
    assert refused.status_code == 409
    blockers = {r["blocker"] for r in refused.json()["detail"]["refusals"]}
    assert blockers == {"undispositioned-node", "stale-link"}


@pytest.mark.asyncio
async def test_a_fully_dispositioned_ticket_closes(
    client: httpx.AsyncClient, graph: CoverageRepository
) -> None:
    """T-310-008 over HTTP: 'nothing changed' is a complete answer."""
    await _link(graph, "AD-100", "CUST-001")
    await _link(graph, "AD-101", "CUST-001")
    await _absorb(client)
    await _confirm(client, "AD-100")
    await _confirm(client, "AD-101")

    closed = await client.post(CLOSE, headers=_OWNER)
    assert closed.status_code == 200
    body = closed.json()
    assert body["closed_by"] == "o.mathieu"
    assert len(body["dispositions"]) == 2
    for disposition in body["dispositions"]:
        assert disposition["kind"] == "confirmed-unchanged"
        assert disposition["justification"] == _WHY
        assert disposition["actor"] == "o.mathieu"
        assert disposition["at"]


@pytest.mark.asyncio
async def test_an_uncovered_requirement_refuses_closure(
    client: httpx.AsyncClient, graph: CoverageRepository, coverage: Coverage
) -> None:
    await _link(graph, "AD-100", "CUST-001")
    await _absorb(client)
    await _confirm(client, "AD-100")
    coverage.covered = False

    refused = await client.post(CLOSE, headers=_OWNER)
    assert refused.status_code == 409
    blockers = {r["blocker"] for r in refused.json()["detail"]["refusals"]}
    assert blockers == {"uncovered-requirement"}
