# =============================================================================
# File: test_pipeline_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_pipeline_flow.py
# Description: Integration tests — full pipeline runs against REAL ArangoDB,
#              REAL C8 client routed to a scripted ASGI LiteLLM mock, and
#              the REAL in-process dispatcher. Exercises phase advancement,
#              Gate A approval, Gate B/C evaluation, three-fix rule.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c4_orchestrator.events.null_publisher import NullPublisher
from ay_platform_core.c4_orchestrator.models import Phase, RunStatus
from tests.integration.c4_orchestrator.conftest import ScriptedLLM

pytestmark = pytest.mark.integration

_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-a",
    "X-User-Roles": "project_editor,admin",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _done(output: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "DONE", "output": output or {}}


def _done_with_gate_b_evidence() -> dict[str, Any]:
    """Implementer payload that passes Gate B (validation exists & runs red)."""
    return {
        "status": "DONE",
        "output": {
            "gate_b_evidence": {
                "artifact_id": "widget-1",
                "validation_artifact_exists": True,
                "validation_runs_red": True,
                "evidence_timestamp": datetime.now(UTC).isoformat(),
            },
        },
    }


def _done_with_gate_c_evidence() -> dict[str, Any]:
    """Reviewer payload that passes Gate C (fresh green)."""
    past = datetime.now(UTC) - timedelta(seconds=5)
    now = datetime.now(UTC)
    return {
        "status": "DONE",
        "output": {
            "gate_c_evidence": {
                "artifact_id": "widget-1",
                "validation_runs_green": True,
                "evidence_timestamp": now.isoformat(),
                "last_artifact_write": past.isoformat(),
            },
        },
    }


def _done_with_gate_c_stale() -> dict[str, Any]:
    """Reviewer payload where evidence is older than last artifact write."""
    artifact_ts = datetime.now(UTC)
    evidence_ts = artifact_ts - timedelta(seconds=10)  # older, stale
    return {
        "status": "DONE",
        "output": {
            "gate_c_evidence": {
                "artifact_id": "widget-1",
                "validation_runs_green": True,
                "evidence_timestamp": evidence_ts.isoformat(),
                "last_artifact_write": artifact_ts.isoformat(),
            },
        },
    }


# ---------------------------------------------------------------------------
# Pipeline reaches plan phase and waits for approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_advances_to_plan_and_pauses(
    c4_app: FastAPI,
    scripted_llm: ScriptedLLM,
) -> None:
    # Brainstorm DONE, Spec DONE, Plan DONE — should reach PLAN and wait.
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan

    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-1",
                "initial_prompt": "build widget X",
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["current_phase"] == Phase.PLAN.value
    assert body["status"] == RunStatus.RUNNING.value


@pytest.mark.asyncio
async def test_gate_a_approval_advances_to_generate(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan
    scripted_llm.enqueue(_done_with_gate_b_evidence())  # generate
    scripted_llm.enqueue(_done_with_gate_c_evidence())  # review

    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-1",
                "initial_prompt": "build widget X",
            },
            headers=_HEADERS,
        )
        run_id = resp.json()["run_id"]
        approve = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_HEADERS,
        )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["status"] == RunStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# Gate B failure triggers a retry; three consecutive failures BLOCKS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_b_three_failures_trigger_three_fix_rule(
    c4_app: FastAPI, scripted_llm: ScriptedLLM, c4_publisher: NullPublisher,
) -> None:
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan
    # Three generate attempts that all fail Gate B (missing validation).
    for _ in range(3):
        scripted_llm.enqueue({
            "status": "DONE",
            "output": {
                "gate_b_evidence": {
                    "artifact_id": "widget-1",
                    "validation_artifact_exists": False,
                    "validation_runs_red": False,
                },
            },
        })

    async with _client(c4_app) as client:
        run = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-fail",
                "initial_prompt": "build widget X",
            },
            headers=_HEADERS,
        )
        run_id = run.json()["run_id"]
        final = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_HEADERS,
        )
    body = final.json()
    assert body["status"] == RunStatus.BLOCKED.value

    # Three-fix rule emits review.requested once the third attempt fails.
    subjects = [s for s, _ in c4_publisher.published]
    assert any("review.requested" in s for s in subjects)
    assert any("run.blocked" in s for s in subjects)


# ---------------------------------------------------------------------------
# Admin resume / abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_resume_marks_run_completed(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    # Block the run first — enqueue a BLOCKED completion on brainstorm.
    scripted_llm.enqueue({
        "status": "BLOCKED",
        "output": {},
        "blocker": {"reason": "testing abort"},
    })
    async with _client(c4_app) as client:
        start = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-abort",
                "initial_prompt": "x",
            },
            headers=_HEADERS,
        )
        run_id = start.json()["run_id"]
        # Abort via admin endpoint
        resume = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/resume",
            json={"strategy": "abort"},
            headers=_HEADERS,
        )
    assert resume.status_code == 200
    assert resume.json()["status"] == RunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_resume_without_admin_role_denied(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    scripted_llm.enqueue({
        "status": "BLOCKED",
        "output": {},
        "blocker": {"reason": "testing admin"},
    })
    async with _client(c4_app) as client:
        start = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-no-admin",
                "initial_prompt": "x",
            },
            headers=_HEADERS,
        )
        run_id = start.json()["run_id"]
        resume = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/resume",
            json={"strategy": "abort"},
            headers={
                "X-User-Id": "bob",
                "X-Tenant-Id": "t",
                "X-User-Roles": "project_editor",  # no 'admin'
            },
        )
    assert resume.status_code == 403


# ---------------------------------------------------------------------------
# One active run per session (R-200-002)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_run_in_same_session_rejected(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    # First run reaches PLAN and pauses (not terminal, still running).
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan

    async with _client(c4_app) as client:
        first = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-dup",
                "initial_prompt": "first",
            },
            headers=_HEADERS,
        )
        assert first.status_code == 201
        # Try to start a second run on the same session while the first is running.
        second = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-1",
                "session_id": "s-dup",
                "initial_prompt": "second",
            },
            headers=_HEADERS,
        )
    assert second.status_code == 409
    assert "another run is active" in second.json()["detail"]


# ---------------------------------------------------------------------------
# Missing forward-auth headers denied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_x_user_id_returns_401(c4_app: FastAPI) -> None:
    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={"project_id": "p-1", "session_id": "s", "initial_prompt": "x"},
            headers={"X-Tenant-Id": "t", "X-User-Roles": "project_editor"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_x_tenant_id_returns_401(c4_app: FastAPI) -> None:
    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={"project_id": "p-1", "session_id": "s", "initial_prompt": "x"},
            headers={"X-User-Id": "alice", "X-User-Roles": "project_editor"},
        )
    assert resp.status_code == 401
