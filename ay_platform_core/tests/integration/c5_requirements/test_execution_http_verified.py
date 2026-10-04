# =============================================================================
# File: test_execution_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_execution_http_verified.py
# Description: The negotiated-piloting REST surface — 310-SPEC §4.9.
#
#              THE GATE THIS FILE GUARDS. R-310-170 requires a HUMAN to
#              ratify before execution. Over HTTP that is two assertions, and
#              both matter: an editor CANNOT ratify (otherwise an agent
#              approves its own plan), and `begin` is refused while the
#              current version is unratified — including after an amendment.
#
#              Paths are FULL LITERALS: the functional-coverage matcher
#              scans test sources for the catalogued path, and implicit
#              concatenation hides a route from it.
#
# @relation validates:R-310-170
# @relation validates:R-310-171
# @relation validates:R-310-173
# @relation validates:R-310-174
# @relation validates:R-310-175
# @relation validates:R-310-176
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator, Sequence

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.execution.estimation import UnitFeatures
from ay_platform_core.c5_requirements.execution.repository import (
    ExecutionRepository,
)
from ay_platform_core.c5_requirements.execution.router import (
    router as execution_router,
)
from ay_platform_core.c5_requirements.execution.service import ExecutionService
from ay_platform_core.c5_requirements.execution.storage import ExecutionStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

PLANS = "/api/v1/projects/adas/plans"
PLAN = "/api/v1/projects/adas/plans/TP-0114"
PROPOSE = "/api/v1/projects/adas/plans/TP-0114/propose"
RATIFY = "/api/v1/projects/adas/plans/TP-0114/ratify"
AMEND = "/api/v1/projects/adas/plans/TP-0114/amend"
VERSION_ONE = "/api/v1/projects/adas/plans/TP-0114/versions/1"
BEGIN = "/api/v1/projects/adas/plans/TP-0114/steps/st1/begin"
CONSUMPTION = "/api/v1/projects/adas/plans/TP-0114/steps/st1/consumption"
COMPLETE = "/api/v1/projects/adas/plans/TP-0114/steps/st1/complete"
FAIL = "/api/v1/projects/adas/plans/TP-0114/steps/st1/fail"
REPORT = "/api/v1/projects/adas/plans/TP-0114/report"

_OWNER = {"X-User-Id": "o.mathieu", "X-User-Roles": "project_owner"}
_EDITOR = {"X-User-Id": "agent.planner", "X-User-Roles": "project_editor"}
_VIEWER = {"X-User-Id": "v.iewer", "X-User-Roles": "project_viewer"}

_UNITS = ("CT-001", "CT-002", "CT-003", "CT-004")
_PROPOSAL = {
    "batch": {"kind": "change_set", "source": "CUST-2026-W14", "count": 4},
    "units": list(_UNITS),
}
_WHY = "The batchable estimate was wrong by an order of magnitude."


class Features:
    """Injected feature lookup, driven by the test."""

    def __init__(self) -> None:
        self.table: dict[str, UnitFeatures] = {
            unit: UnitFeatures(
                unit_id=unit, impact_nodes=2, layers_crossed=1, containers=1
            )
            for unit in _UNITS
        }

    async def __call__(
        self, project_id: str, units: tuple[str, ...]
    ) -> Sequence[UnitFeatures]:
        return [self.table[unit] for unit in units if unit in self.table]


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[FastAPI, Features]]:
    db_name = f"c5_exec_http_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ExecutionRepository(db)
        repo._ensure_collections_sync()
        features = Features()
        app = FastAPI()
        app.include_router(execution_router)
        app.state.execution_service = ExecutionService(
            ExecutionStorage(c5_storage), repo, features
        )
        yield app, features
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
async def client(
    wired: tuple[FastAPI, Features],
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=wired[0])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
def features(wired: tuple[FastAPI, Features]) -> Features:
    return wired[1]


async def _propose(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(PROPOSE, json=_PROPOSAL, headers=_EDITOR)


async def _ratify(client: httpx.AsyncClient, version: int = 1) -> httpx.Response:
    return await client.post(
        RATIFY, json={"plan_version": version}, headers=_OWNER
    )


# ---------------------------------------------------------------------------
# Auth — the role split IS R-310-170
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post(PROPOSE, json=_PROPOSAL)).status_code == 401
    assert (await client.get(PLANS)).status_code == 401


@pytest.mark.asyncio
async def test_an_editor_cannot_ratify_its_own_plan(
    client: httpx.AsyncClient,
) -> None:
    """The whole supervision model: an agent proposes, a human approves."""
    await _propose(client)
    refused = await client.post(RATIFY, json={"plan_version": 1}, headers=_EDITOR)
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_an_editor_cannot_amend(client: httpx.AsyncClient) -> None:
    """An amendment changes approved terms, so it needs that authority."""
    await _propose(client)
    refused = await client.post(
        AMEND,
        json={
            "step_id": "st1",
            "partitions": [list(_UNITS[:2]), list(_UNITS[2:])],
            "rationale": _WHY,
        },
        headers=_EDITOR,
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_a_viewer_cannot_propose_or_execute(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.post(PROPOSE, json=_PROPOSAL, headers=_VIEWER)).status_code == 403
    await _propose(client)
    assert (await client.post(BEGIN, headers=_VIEWER)).status_code == 403


@pytest.mark.asyncio
async def test_reads_are_merely_authenticated(client: httpx.AsyncClient) -> None:
    await _propose(client)
    assert (await client.get(PLANS, headers=_VIEWER)).status_code == 200
    assert (await client.get(PLAN, headers=_VIEWER)).status_code == 200
    assert (await client.get(VERSION_ONE, headers=_VIEWER)).status_code == 200


# ---------------------------------------------------------------------------
# Proposal — R-310-171
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_proposal_returns_a_priced_decomposition(
    client: httpx.AsyncClient,
) -> None:
    response = await _propose(client)
    assert response.status_code == 201
    body = response.json()
    assert body["steps"]
    for step in body["steps"]:
        assert step["estimate"]["tokens"] > 0
        assert step["estimate"]["review_items"] > 0


@pytest.mark.asyncio
async def test_a_proposal_over_nothing_is_refused_with_409(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        PROPOSE,
        json={
            "batch": {"kind": "change_set", "source": "CUST-2026-W14", "count": 0},
            "units": [],
        },
        headers=_EDITOR,
    )
    assert response.status_code == 409
    assert "R-310-171" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_unit_the_graph_does_not_know_is_refused(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        PROPOSE,
        json={
            "batch": {"kind": "change_set", "source": "W14", "count": 1},
            "units": ["CT-404"],
        },
        headers=_EDITOR,
    )
    assert response.status_code == 409
    assert "no features for" in response.json()["detail"]


@pytest.mark.asyncio
async def test_re_proposing_the_same_plan_is_refused(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    assert (await _propose(client)).status_code == 409


@pytest.mark.asyncio
async def test_an_unknown_plan_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(PLAN, headers=_VIEWER)).status_code == 404


# ---------------------------------------------------------------------------
# Ratification — R-310-170 / R-310-175
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ratification_records_the_human_and_the_version(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    body = (await _ratify(client)).json()
    assert body["ratification"]["actor"] == "o.mathieu"
    assert body["ratification"]["plan_version"] == 1
    assert body["ratification"]["at"]


@pytest.mark.asyncio
async def test_ratifying_a_version_the_caller_did_not_read_is_refused(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    refused = await _ratify(client, version=9)
    assert refused.status_code == 409
    assert "R-310-170" in refused.json()["detail"]


# ---------------------------------------------------------------------------
# Execution needs ratification — R-310-170
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_beginning_an_unratified_plan_is_423_locked(
    client: httpx.AsyncClient,
) -> None:
    """423, not 409: blocked by a missing human decision, not a conflict."""
    await _propose(client)
    refused = await client.post(BEGIN, headers=_EDITOR)
    assert refused.status_code == 423
    assert "R-310-170" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_beginning_a_ratified_plan_succeeds(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    response = await client.post(BEGIN, headers=_EDITOR)
    assert response.status_code == 200
    assert response.json()["steps"][0]["state"] == "running"


@pytest.mark.asyncio
async def test_an_amendment_revokes_the_ratification_over_http(
    client: httpx.AsyncClient,
) -> None:
    """R-310-173 + R-310-170: amending must not carry approval forward."""
    await _propose(client)
    await _ratify(client)
    amended = await client.post(
        AMEND,
        json={
            "step_id": "st1",
            "partitions": [list(_UNITS[:2]), list(_UNITS[2:])],
            "rationale": _WHY,
        },
        headers=_OWNER,
    )
    assert amended.status_code == 200
    assert amended.json()["version"] == 2

    blocked = await client.post(
        "/api/v1/projects/adas/plans/TP-0114/steps/st1.1/begin", headers=_EDITOR
    )
    assert blocked.status_code == 423


@pytest.mark.asyncio
async def test_an_amendment_that_loses_a_unit_is_refused(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    refused = await client.post(
        AMEND,
        json={
            "step_id": "st1",
            "partitions": [[_UNITS[0]], [_UNITS[1]]],
            "rationale": _WHY,
        },
        headers=_OWNER,
    )
    assert refused.status_code == 409
    assert "lost" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_an_amendment_needs_a_rationale(client: httpx.AsyncClient) -> None:
    await _propose(client)
    refused = await client.post(
        AMEND,
        json={
            "step_id": "st1",
            "partitions": [list(_UNITS[:2]), list(_UNITS[2:])],
            "rationale": "oops",
        },
        headers=_OWNER,
    )
    assert refused.status_code == 422


# ---------------------------------------------------------------------------
# R-310-174 — suspension on overrun
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reporting_consumption_within_the_estimate_keeps_running(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    await client.post(BEGIN, headers=_EDITOR)
    response = await client.post(
        CONSUMPTION, json={"tokens": 10, "review_items": 1}, headers=_EDITOR
    )
    assert response.status_code == 200
    assert response.json()["steps"][0]["state"] == "running"


@pytest.mark.asyncio
async def test_reporting_an_overrun_suspends_and_says_why(
    client: httpx.AsyncClient,
) -> None:
    """200 with the suspension in the body: a suspension is the outcome of
    the report, not a rejection of it."""
    await _propose(client)
    await _ratify(client)
    await client.post(BEGIN, headers=_EDITOR)
    response = await client.post(
        CONSUMPTION,
        json={"tokens": 99_000_000, "review_items": 99_000},
        headers=_EDITOR,
    )
    assert response.status_code == 200
    step = response.json()["steps"][0]
    assert step["state"] == "suspended"
    assert "tokens" in step["suspended_reason"]


@pytest.mark.asyncio
async def test_a_negative_consumption_is_refused_by_the_body(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    refused = await client.post(
        CONSUMPTION, json={"tokens": -1}, headers=_EDITOR
    )
    assert refused.status_code == 422


@pytest.mark.asyncio
async def test_completing_then_reusing_a_step_is_refused(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    assert (await client.post(COMPLETE, headers=_EDITOR)).status_code == 200
    refused = await client.post(BEGIN, headers=_EDITOR)
    assert refused.status_code == 409
    assert "terminal step" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_a_step_can_be_failed_distinctly_from_suspended(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    response = await client.post(FAIL, headers=_EDITOR)
    assert response.status_code == 200
    assert response.json()["steps"][0]["state"] == "failed"


# ---------------------------------------------------------------------------
# R-310-175 — the approved version stays readable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_approved_version_is_readable_after_an_amendment(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    await client.post(
        AMEND,
        json={
            "step_id": "st1",
            "partitions": [list(_UNITS[:2]), list(_UNITS[2:])],
            "rationale": _WHY,
        },
        headers=_OWNER,
    )
    approved = (await client.get(VERSION_ONE, headers=_VIEWER)).json()
    assert approved["version"] == 1
    assert [step["step_id"] for step in approved["steps"]] == ["st1"]
    assert approved["ratification"]["plan_version"] == 1


@pytest.mark.asyncio
async def test_an_unstored_version_is_404(client: httpx.AsyncClient) -> None:
    await _propose(client)
    missing = await client.get(
        "/api/v1/projects/adas/plans/TP-0114/versions/9", headers=_VIEWER
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_listing_can_be_narrowed_to_plans_awaiting_ratification(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    awaiting = await client.get(
        PLANS, params={"awaiting_ratification": True}, headers=_VIEWER
    )
    assert awaiting.json()["count"] == 1
    await _ratify(client)
    after = await client.get(
        PLANS, params={"awaiting_ratification": True}, headers=_VIEWER
    )
    assert after.json()["count"] == 0


# ---------------------------------------------------------------------------
# R-310-176 — the treatment report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_report_is_refused_while_a_step_is_outstanding(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    refused = await client.post(REPORT, json={}, headers=_EDITOR)
    assert refused.status_code == 409
    assert "R-310-176" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_a_report_on_a_completed_plan_names_what_was_done(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    await client.post(COMPLETE, headers=_EDITOR)
    response = await client.post(
        REPORT,
        json={
            "containers_modified": ["030-ARCH"],
            "objects_created": ["AD-100"],
            "review_outcomes": {"accepted": 3, "auto-accepted": 1},
            "coverage_before": 10,
            "coverage_after": 14,
        },
        headers=_EDITOR,
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["requirements_processed"]) == 4
    assert body["review_outcomes"]["auto-accepted"] == 1


@pytest.mark.asyncio
async def test_a_report_is_readable_once_produced(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    await _ratify(client)
    await client.post(COMPLETE, headers=_EDITOR)
    await client.post(REPORT, json={}, headers=_EDITOR)
    fetched = await client.get(REPORT, headers=_VIEWER)
    assert fetched.status_code == 200
    assert fetched.json()["plan_id"] == "TP-0114"


@pytest.mark.asyncio
async def test_no_report_before_it_is_produced_is_404(
    client: httpx.AsyncClient,
) -> None:
    await _propose(client)
    assert (await client.get(REPORT, headers=_VIEWER)).status_code == 404
