# =============================================================================
# File: test_generate_materialisation.py
# Version: 1
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_generate_materialisation.py
# Description: End-to-end integration test for R-200-150..152. Drives a
#              full pipeline run (brainstorm → spec → plan → gate A →
#              generate → review) with a scripted LLM that returns a
#              generate-phase `output.files` payload, then asserts:
#              (1) files land on the artifacts surface via the MinIO
#                  backend (listable through GET /artifacts/runs/{id}/tree
#                  + retrievable via GET /artifacts/runs/{id}/blob),
#              (2) the Gitea push fires (FakeGitea has the files +
#                  commits at owner svc-{tenant}-{project}),
#              (3) the artifact run_id is the orchestrator run_id (R-200-151).
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from ay_platform_core.c4_orchestrator.models import Phase, RunStatus
from tests.integration.c2_auth.test_gitea_provisioning import _FakeGiteaClient
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


def _done(output: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": "DONE", "output": output or {}}


def _generate_payload_with_files() -> dict[str, Any]:
    """Implementer payload that includes both Gate B evidence AND the
    materialisable files list per R-200-150."""
    return {
        "status": "DONE",
        "output": {
            "files": [
                {
                    "path": "src/widget.py",
                    "content": "def widget() -> str:\n    return 'hi'\n",
                },
                {
                    "path": "tests/test_widget.py",
                    "content": (
                        "from src.widget import widget\n\n"
                        "def test_widget_returns_hi() -> None:\n"
                        "    assert widget() == 'hi'\n"
                    ),
                },
            ],
            "gate_b_evidence": {
                "artifact_id": "tests/test_widget.py",
                "validation_artifact_exists": True,
                "validation_runs_red": True,
                "evidence_timestamp": datetime.now(UTC).isoformat(),
            },
        },
    }


def _review_payload_passing_gate_c() -> dict[str, Any]:
    past = datetime.now(UTC) - timedelta(seconds=5)
    now = datetime.now(UTC)
    return {
        "status": "DONE",
        "output": {
            "gate_c_evidence": {
                "artifact_id": "tests/test_widget.py",
                "validation_runs_green": True,
                "evidence_timestamp": now.isoformat(),
                "last_artifact_write": past.isoformat(),
            },
        },
    }


# ---------------------------------------------------------------------------
# R-200-151 — generate output.files materialises onto artifacts surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_materialises_files_and_pushes_to_gitea(
    c4_app_with_artifacts: tuple[FastAPI, ArtifactsService, _FakeGiteaClient],
    scripted_llm: ScriptedLLM,
) -> None:
    app, _artifacts, fake_gitea = c4_app_with_artifacts
    # Pipeline : brainstorm → spec → plan (gate A waits) → generate → review.
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan — pauses at gate A
    scripted_llm.enqueue(_generate_payload_with_files())  # generate
    scripted_llm.enqueue(_review_payload_passing_gate_c())  # review

    async with _client(app) as c:
        # Start the run.
        start = await c.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "proj-mat",
                "session_id": "sess-mat",
                "initial_prompt": "Generate a tiny widget module",
            },
            headers=_HEADERS,
        )
        assert start.status_code == 201, start.text
        run_id = start.json()["run_id"]
        assert start.json()["current_phase"] == Phase.PLAN.value

        # Approve gate A → generate runs → materialisation fires →
        # review runs → pipeline completes.
        approve = await c.post(
            f"/api/v1/orchestrator/runs/{run_id}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_HEADERS,
        )
        assert approve.status_code == 200, approve.text
        assert approve.json()["status"] == RunStatus.COMPLETED.value

        # 1. Files visible through the artifacts read API.
        tree = await c.get(
            f"/api/v1/projects/proj-mat/artifacts/runs/{run_id}/tree",
            headers=_HEADERS,
        )
        assert tree.status_code == 200, tree.text
        paths = {n["path"] for n in tree.json()["nodes"]}
        assert paths == {"src/widget.py", "tests/test_widget.py"}

        # 2. Blob content matches what the agent returned (UTF-8 round-trip).
        widget = await c.get(
            f"/api/v1/projects/proj-mat/artifacts/runs/{run_id}/blob",
            headers=_HEADERS,
            params={"path": "src/widget.py"},
        )
        assert widget.status_code == 200
        assert widget.text == "def widget() -> str:\n    return 'hi'\n"

        # 3. Runs listing surfaces the orchestrator run.
        runs = await c.get(
            "/api/v1/projects/proj-mat/artifacts/runs", headers=_HEADERS,
        )
        assert runs.status_code == 200
        run_rows = runs.json()["runs"]
        assert any(r["run_id"] == run_id for r in run_rows)
        ours = next(r for r in run_rows if r["run_id"] == run_id)
        assert ours["status"] == "completed"
        assert ours["file_count"] == 2

    # 4. Gitea push side-effects observed on the stub. Owner is the
    #    deterministic svc-{tenant}-{project} (R-200-146).
    expected_owner = "svc-tenant-a-proj-mat"
    key = (expected_owner, "proj-mat")
    assert key in fake_gitea.files, fake_gitea.files
    pushed = fake_gitea.files[key]
    assert pushed["src/widget.py"] == b"def widget() -> str:\n    return 'hi'\n"
    assert "tests/test_widget.py" in pushed
    # 5. One commit per file (R-200-146 per-file commit convention).
    commits = fake_gitea.commits.get(key, [])
    assert len(commits) == 2


# ---------------------------------------------------------------------------
# R-200-152 — gate B failure does NOT materialise (avoids partial writes
# from buggy generate output) AND ArtifactsService failures don't block.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_b_failure_skips_materialisation(
    c4_app_with_artifacts: tuple[FastAPI, ArtifactsService, _FakeGiteaClient],
    scripted_llm: ScriptedLLM,
) -> None:
    """If gate B fails on the first attempt, the orchestrator retries
    the generate phase. Materialisation SHALL NOT fire on the failed
    attempt — only on the first successful gate-B-passing generate."""
    app, _artifacts, fake_gitea = c4_app_with_artifacts
    # First generate attempt = gate B fails (validation_runs_red=False).
    # Second generate attempt = passes + emits files.
    bad_payload: dict[str, Any] = {
        "status": "DONE",
        "output": {
            "files": [
                {
                    "path": "should_not_be_pushed.py",
                    "content": "# this attempt failed gate B\n",
                },
            ],
            "gate_b_evidence": {
                "artifact_id": "should_not_be_pushed.py",
                "validation_artifact_exists": True,
                "validation_runs_red": False,  # gate B fails
                "evidence_timestamp": datetime.now(UTC).isoformat(),
            },
        },
    }
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan
    scripted_llm.enqueue(bad_payload)  # generate #1 — gate B fails
    scripted_llm.enqueue(_generate_payload_with_files())  # generate #2 — passes
    scripted_llm.enqueue(_review_payload_passing_gate_c())  # review

    async with _client(app) as c:
        start = await c.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "proj-gateb",
                "session_id": "sess-gateb",
                "initial_prompt": "Generate widget — retry path",
            },
            headers=_HEADERS,
        )
        assert start.status_code == 201
        run_id = start.json()["run_id"]
        approve = await c.post(
            f"/api/v1/orchestrator/runs/{run_id}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_HEADERS,
        )
        assert approve.status_code == 200
        assert approve.json()["status"] == RunStatus.COMPLETED.value

    # The failed-gate-B attempt's file SHALL NOT have been pushed.
    key = ("svc-tenant-a-proj-gateb", "proj-gateb")
    pushed = fake_gitea.files.get(key, {})
    assert "should_not_be_pushed.py" not in pushed
    # The successful attempt's files SHALL be present.
    assert "src/widget.py" in pushed
    assert "tests/test_widget.py" in pushed


# ---------------------------------------------------------------------------
# R-200-021 v3 — dispatcher tolerant parser : the same pipeline SHALL
# succeed end-to-end when the LLM wraps its envelope in markdown fences
# (the typical qwen2.5:3b output pattern that BLOCKED phase 1 with the
# strict v2 parser).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_completes_with_fenced_llm_output(
    c4_app_with_artifacts: tuple[FastAPI, ArtifactsService, _FakeGiteaClient],
    scripted_llm: ScriptedLLM,
) -> None:
    """Same scripted envelopes as the happy-path test, but the mock
    LLM wraps them in ```json fences and prepends prose. With the v2
    strict parser every phase would BLOCK ; the v3 tolerant parser
    extracts the envelope and the pipeline reaches COMPLETED with
    files materialised. Regression test for the 2026-05-13 incident."""
    app, _artifacts, fake_gitea = c4_app_with_artifacts
    scripted_llm.style = "fenced"  # type: ignore[attr-defined]
    scripted_llm.enqueue(_done())  # brainstorm
    scripted_llm.enqueue(_done())  # spec
    scripted_llm.enqueue(_done())  # plan
    scripted_llm.enqueue(_generate_payload_with_files())  # generate
    scripted_llm.enqueue(_review_payload_passing_gate_c())  # review

    async with _client(app) as c:
        start = await c.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": "proj-fenced",
                "session_id": "sess-fenced",
                "initial_prompt": "Generate widget under noisy LLM",
            },
            headers=_HEADERS,
        )
        assert start.status_code == 201, start.text
        run_id = start.json()["run_id"]
        assert start.json()["current_phase"] == Phase.PLAN.value

        approve = await c.post(
            f"/api/v1/orchestrator/runs/{run_id}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_HEADERS,
        )
        assert approve.status_code == 200, approve.text
        assert approve.json()["status"] == RunStatus.COMPLETED.value

        tree = await c.get(
            f"/api/v1/projects/proj-fenced/artifacts/runs/{run_id}/tree",
            headers=_HEADERS,
        )
        assert tree.status_code == 200
        paths = {n["path"] for n in tree.json()["nodes"]}
        assert paths == {"src/widget.py", "tests/test_widget.py"}

    key = ("svc-tenant-a-proj-fenced", "proj-fenced")
    assert key in fake_gitea.files
    assert "src/widget.py" in fake_gitea.files[key]
