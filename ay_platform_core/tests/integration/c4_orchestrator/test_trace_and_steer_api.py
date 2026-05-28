# =============================================================================
# File: test_trace_and_steer_api.py
# Version: 2
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_trace_and_steer_api.py
# Description: Integration tests for Tranche B C4 endpoints (R-200-200..205) :
#                - `GET /runs/{id}` includes the sliding-window `trace` ;
#                - `GET /runs/{id}/trace?before=<ts>` paginates back ;
#                - `POST /runs/{id}/steer` queues a hint, consumed at the
#                  next agent dispatch and recorded as a STEER_APPLIED
#                  event in the trace ledger.
#              Real ArangoDB, real C8 client + ASGI LiteLLM mock — same
#              fixtures as test_pipeline_flow.py.
#
# @relation validates:R-200-200
# @relation validates:R-200-201
# @relation validates:R-200-202
# @relation validates:R-200-203
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c4_orchestrator.models import Phase, RunStatus
from tests.integration.c4_orchestrator.conftest import ScriptedLLM

pytestmark = pytest.mark.integration

_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-a",
    "X-User-Roles": "project_editor,admin",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


def _done() -> dict[str, object]:
    return {"status": "DONE", "output": {}}


@pytest.mark.asyncio
async def test_run_creation_populates_trace_with_dispatch_and_boundaries(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    """A run that reaches PLAN (3 dispatches, 2 phase boundaries) SHALL
    surface AGENT_DISPATCH start/end + PHASE_BOUNDARY events in the
    public `trace` window, newest-first."""
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan

    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-trace-1",
                "session_id": "s-trace-1",
                "initial_prompt": "build widget X",
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    trace = body["trace"]
    kinds = [ev["kind"] for ev in trace]
    # Newest-first : the most recent event must be the AGENT_DISPATCH
    # "end" for the plan phase (run pauses on Gate A waiting for approval).
    assert kinds[0] == "agent-dispatch"
    assert trace[0]["phase"] == Phase.PLAN.value
    assert trace[0].get("ok") is True
    # We had two phase boundaries crossed before parking on PLAN
    # (brainstorm→spec and spec→plan).
    assert kinds.count("phase-boundary") == 2
    # Every agent dispatch is logged twice (start + end), 3 phases x 2.
    assert kinds.count("agent-dispatch") == 6


@pytest.mark.asyncio
async def test_steer_queues_then_consumes_on_next_dispatch(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    """POST /steer SHALL queue the hint ; the next agent dispatch (here,
    triggered by /feedback approving the plan) SHALL drain the queue,
    embed the hint in the prompt, and append a STEER_APPLIED event."""
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan
    scripted_llm.enqueue(_done())  # generate (post-approval)

    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-steer",
                "session_id": "s-steer",
                "initial_prompt": "build widget X",
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        # Queue a steer while the run is paused on PLAN (status=running).
        steer = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/steer",
            json={"message": "focus on REST, not gRPC"},
            headers=_HEADERS,
        )
        assert steer.status_code == 200, steer.text

        # Approve the plan — this triggers the next agent dispatch
        # (generate phase), which drains the queue.
        approve = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_HEADERS,
        )
        assert approve.status_code == 200, approve.text

        # The scripted mock captured the prompt of each call. The steer is
        # drained at the FIRST post-approval dispatch (generate) ; the
        # pipeline may dispatch again after that (a gate-B retry on the
        # bare DONE response, or the review phase), so the steer block is
        # NOT necessarily on the last call. Locate the generate dispatch.
        generate_call = next(
            c for c in scripted_llm.calls_seen
            if any(
                m["role"] == "user" and "Phase: generate" in m["content"]
                for m in c["messages"]
            )
        )
        user_prompt = next(
            m["content"] for m in generate_call["messages"] if m["role"] == "user"
        )
        assert "<operator-steering>" in user_prompt
        assert "focus on REST, not gRPC" in user_prompt

        # And the trace ledger MUST carry a steer-applied event.
        final = await client.get(
            f"/api/v1/orchestrator/runs/{run_id}", headers=_HEADERS,
        )
        kinds = [ev["kind"] for ev in final.json()["trace"]]
        assert "steer-applied" in kinds


@pytest.mark.asyncio
async def test_steer_rejected_when_run_not_running(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    """A steer on a completed (or blocked) run SHALL 409 — operator
    steering is a hint for live runs, not a way to mutate terminal state."""
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan
    scripted_llm.enqueue({
        "status": "DONE",
        "output": {
            "gate_b_evidence": {
                "artifact_id": "x",
                "validation_artifact_exists": True,
                "validation_runs_red": True,
                "evidence_timestamp": "2026-05-20T12:00:00+00:00",
            },
        },
    })  # generate
    scripted_llm.enqueue({
        "status": "DONE",
        "output": {
            "gate_c_evidence": {
                "artifact_id": "x",
                "validation_runs_green": True,
                "evidence_timestamp": "2026-05-20T12:00:05+00:00",
                "last_artifact_write": "2026-05-20T12:00:00+00:00",
            },
        },
    })  # review

    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-steer-end",
                "session_id": "s-steer-end",
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
        assert approve.json()["status"] == RunStatus.COMPLETED.value

        # Try to steer a completed run.
        steer = await client.post(
            f"/api/v1/orchestrator/runs/{run_id}/steer",
            json={"message": "too late"},
            headers=_HEADERS,
        )
        assert steer.status_code == 409


@pytest.mark.asyncio
async def test_trace_pagination_returns_back_in_time_slice(
    c4_app: FastAPI, scripted_llm: ScriptedLLM,
) -> None:
    """`GET /trace?before=<ts>&limit=N` SHALL return events strictly older
    than `before`, newest-first."""
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan

    async with _client(c4_app) as client:
        resp = await client.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "p-trace-p",
                "session_id": "s-trace-p",
                "initial_prompt": "build widget X",
            },
            headers=_HEADERS,
        )
        run_id = resp.json()["run_id"]
        full = resp.json()["trace"]
        assert len(full) >= 4

        # Use the timestamp of the third-most-recent event as the pivot.
        pivot_ts = full[2]["ts"]
        page = await client.get(
            f"/api/v1/orchestrator/runs/{run_id}/trace",
            params={"before": pivot_ts, "limit": 50},
            headers=_HEADERS,
        )
        assert page.status_code == 200
        slice_ = page.json()
        assert all(ev["ts"] < pivot_ts for ev in slice_)
        # Newest-first inside the slice.
        assert slice_ == sorted(slice_, key=lambda ev: ev["ts"], reverse=True)


@pytest.mark.asyncio
async def test_trace_404_for_unknown_run(c4_app: FastAPI) -> None:
    async with _client(c4_app) as client:
        resp = await client.get(
            "/api/v1/orchestrator/runs/does-not-exist/trace",
            headers=_HEADERS,
        )
    assert resp.status_code == 404
