# =============================================================================
# File: test_golden_path_orchestrator.py
# Version: 1
# Path: ay_platform_core/tests/system/test_golden_path_orchestrator.py
# Description: Golden-path system test — exercises the 5-phase orchestrator
#              pipeline end-to-end against the running docker-compose stack:
#                   Traefik → C2 auth → C3 conv → C4 orchestrator
#                                                  ↓
#                                                  C5 requirements (seed)
#                                                  mock LLM (scripted)
#                                                  C6 validation (trigger)
#
#              Per session directive: tests go through Traefik on port 80.
#              The mock LLM admin endpoint (host port 8001) is the sole
#              test-only side channel — used to script LLM responses and
#              reset state between tests.
#
#              Prerequisites (once):
#                `./ay_platform_core/scripts/e2e_stack.sh up && … seed`.
# =============================================================================

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.system


# ---------------------------------------------------------------------------
# Scripted LLM response helpers (mirror the e2e conftest shapes)
# ---------------------------------------------------------------------------


def _brainstorm_done() -> dict[str, Any]:
    return {
        "envelope": {
            "status": "DONE",
            "output": {"proposal": "widget the frobulator"},
        }
    }


def _spec_done() -> dict[str, Any]:
    # Pretend the architect agent confirmed the seeded entity R-900-001.
    return {
        "envelope": {
            "status": "DONE",
            "output": {"entities": ["R-900-001"]},
        }
    }


def _plan_done() -> dict[str, Any]:
    return {
        "envelope": {
            "status": "DONE",
            "output": {
                "steps": [{"id": 1, "description": "implement widget"}],
            },
        }
    }


def _generate_done_gate_b() -> dict[str, Any]:
    return {
        "envelope": {
            "status": "DONE",
            "output": {
                "gate_b_evidence": {
                    "artifact_id": "widget-frobulator",
                    "validation_artifact_exists": True,
                    "validation_runs_red": True,
                    "evidence_timestamp": datetime.now(UTC).isoformat(),
                },
            },
        }
    }


def _review_done_gate_c() -> dict[str, Any]:
    past = datetime.now(UTC) - timedelta(seconds=5)
    now = datetime.now(UTC)
    return {
        "envelope": {
            "status": "DONE",
            "output": {
                "gate_c_evidence": {
                    "artifact_id": "widget-frobulator",
                    "validation_runs_green": True,
                    "evidence_timestamp": now.isoformat(),
                    "last_artifact_write": past.isoformat(),
                },
            },
        }
    }


async def _enqueue_full_pipeline(mock_llm_admin: httpx.AsyncClient) -> None:
    """Queue the five successful agent completions for one run."""
    for envelope in (
        _brainstorm_done(),
        _spec_done(),
        _plan_done(),
        _generate_done_gate_b(),
        _review_done_gate_c(),
    ):
        resp = await mock_llm_admin.post("/admin/enqueue", json=envelope)
        assert resp.status_code == 200, resp.text


async def _poll_run(
    gateway_client: httpx.AsyncClient,
    run_id: str,
    auth: dict[str, str],
    *,
    deadline_s: float = 60.0,
) -> dict[str, Any]:
    """Poll GET /orchestrator/runs/<id> until status is terminal or deadline."""
    end = asyncio.get_event_loop().time() + deadline_s
    last_body: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < end:
        resp = await gateway_client.get(
            f"/api/v1/orchestrator/runs/{run_id}", headers=auth
        )
        assert resp.status_code == 200, resp.text
        last_body = resp.json()
        if last_body["status"] in {"completed", "blocked"}:
            return last_body
        await asyncio.sleep(0.5)
    pytest.fail(
        f"run {run_id} never terminated within {deadline_s}s "
        f"(last status={last_body.get('status')!r}, "
        f"phase={last_body.get('current_phase')!r})"
    )


# ---------------------------------------------------------------------------
# Golden path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_golden_path_full_pipeline(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    mock_llm_admin: httpx.AsyncClient,
) -> None:
    """End-to-end: Traefik → C2 token → C3 conversation → C4 run → COMPLETED.

    Uses the deterministic data injected by the seeder (`demo` project with
    the approved entity ``R-900-001``). Each phase consumes one scripted
    response from the mock LLM.
    """
    # 1. Script five successful agent completions for the pipeline.
    await _enqueue_full_pipeline(mock_llm_admin)

    tenant_id = "t-demo"
    session_id = "sess-golden-sys"
    c4_headers = {**auth_headers, "X-Tenant-Id": tenant_id}

    # 2. (Optional sanity) Create a conversation — the UI's normal path.
    conv = await gateway_client.post(
        "/api/v1/conversations",
        json={"title": "Golden system run", "project_id": "demo"},
        headers=auth_headers,
    )
    assert conv.status_code == 201, conv.text

    # 3. Trigger the orchestrator run. C4 starts the brainstorm phase
    # immediately (NOT interactive), then halts at the PLAN phase for Gate A.
    start = await gateway_client.post(
        "/api/v1/orchestrator/runs",
        json={
            "project_id": "demo",
            "session_id": session_id,
            "initial_prompt": "build the frobulator widget",
            "domain": "code",
        },
        headers=c4_headers,
    )
    assert start.status_code == 201, start.text
    run_body = start.json()
    run_id = run_body["run_id"]
    assert run_body["current_phase"] == "plan", (
        f"expected halt at plan-phase gate A, got {run_body['current_phase']}"
    )

    # 4. User approves the plan via feedback — Gate A.
    approve = await gateway_client.post(
        f"/api/v1/orchestrator/runs/{run_id}/feedback",
        json={"phase": "plan", "approved": True},
        headers=c4_headers,
    )
    assert approve.status_code == 200, approve.text

    # 5. Poll to completion (handler returns once the run reaches a terminal
    # state; polling is belt-and-suspenders in case the handler returns
    # early while the background phases are still dispatching).
    final = await _poll_run(gateway_client, run_id, c4_headers)
    assert final["status"] == "completed", (
        f"expected COMPLETED, got {final['status']} at phase {final['current_phase']}"
    )

    # 6. Assert all five scripted completions were consumed by C4 via C8 mock.
    calls = await mock_llm_admin.get("/admin/calls")
    assert calls.status_code == 200, calls.text
    call_log = calls.json()
    assert len(call_log) == 5, (
        f"expected 5 LLM calls across phases, got {len(call_log)}"
    )

    # 7. MinIO root set by C4 for dispatch bundles.
    assert final["minio_root"].startswith("c4-runs/")


@pytest.mark.asyncio
async def test_run_blocks_when_llm_queue_is_empty(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    mock_llm_admin: httpx.AsyncClient,
) -> None:
    """Negative path: with no scripted LLM response, C4 SHALL NOT succeed.

    The mock LLM returns a BLOCKED envelope on empty queue (see
    ``_mock_llm/main.py``). C4 surfaces that as a run-level block. This
    confirms the mock + orchestrator error-propagation are wired correctly.
    """
    # Deliberately DO NOT enqueue anything — the fixture's reset already
    # cleared the queue.
    tenant_id = "t-demo"
    c4_headers = {**auth_headers, "X-Tenant-Id": tenant_id}

    resp = await gateway_client.post(
        "/api/v1/orchestrator/runs",
        json={
            "project_id": "demo",
            "session_id": "sess-empty-queue",
            "initial_prompt": "provoke a block",
            "domain": "code",
        },
        headers=c4_headers,
    )
    assert resp.status_code in (201, 200), resp.text
    body = resp.json()
    run_id = body["run_id"]
    final = await _poll_run(gateway_client, run_id, c4_headers)
    # Either the run itself blocks, or it halts at the plan gate waiting
    # for user feedback. Both are legitimate terminal states for this
    # scenario — the point is that it does NOT reach `completed`.
    assert final["status"] != "completed", (
        "run should not complete when the LLM queue is empty"
    )


@pytest.mark.asyncio
async def test_c6_validation_blocking_finding_visible_via_traefik(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Independent of the orchestrator: a bare module artifact SHALL produce
    a blocking finding from the code-without-requirement check, and that
    finding SHALL be reachable through the Traefik-fronted C6 API.
    """
    trigger = await gateway_client.post(
        "/api/v1/validation/runs",
        json={
            "domain": "code",
            "project_id": "demo",
            "check_ids": ["code-without-requirement"],
            "requirements": [],
            "artifacts": [
                {
                    "path": "src/empty.py",
                    "content": "def x(): pass\n",
                    "is_test": False,
                }
            ],
        },
        headers=auth_headers,
    )
    assert trigger.status_code == 202, trigger.text
    run_id = trigger.json()["run_id"]

    for _ in range(60):
        detail = await gateway_client.get(
            f"/api/v1/validation/runs/{run_id}", headers=auth_headers
        )
        assert detail.status_code == 200
        if detail.json()["status"] == "completed":
            break
        await asyncio.sleep(0.5)
    else:
        pytest.fail(f"validation run {run_id} never completed")

    findings = await gateway_client.get(
        f"/api/v1/validation/runs/{run_id}/findings", headers=auth_headers
    )
    assert findings.status_code == 200
    items = findings.json()["items"]
    assert any(
        f["check_id"] == "code-without-requirement"
        and f["severity"] == "blocking"
        and f["artifact_ref"] == "src/empty.py"
        for f in items
    ), f"expected a blocking code-without-requirement finding; got: {items}"
