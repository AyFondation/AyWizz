# =============================================================================
# File: test_golden_path.py
# Version: 1
# Path: ay_platform_core/tests/e2e/test_golden_path.py
# Description: First cross-component end-to-end test. Exercises:
#              - C2 token issuance (local mode, user auto-provisioned via none-mode)
#              - C3 conversation creation
#              - C5 document seeding with an entity the orchestrator will later
#                consult
#              - C4 pipeline run: brainstorm -> spec -> plan -> (gate A approval)
#                -> generate (gate B) -> review (gate C) -> COMPLETED
#
#              Uses REAL ArangoDB + REAL MinIO + in-process FastAPI apps +
#              scripted LLM mock. No Traefik, no K8s pods — those layers
#              are asserted by their respective contract tests.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from ay_platform_core.c4_orchestrator.models import Phase, RunStatus
from tests.e2e.conftest import PlatformStack

pytestmark = pytest.mark.e2e


def _c3_headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def _c5_headers(user_id: str) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-User-Roles": "project_editor,project_owner",
    }


def _c4_headers(user_id: str, tenant: str) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Tenant-Id": tenant,
        "X-User-Roles": "project_editor,admin",
    }


_SPEC_DOC = """---
document: 300-SPEC-DEMO
version: 1
path: projects/e2e-demo/requirements/300-SPEC-DEMO.md
language: en
status: draft
---

# Demo spec for the e2e run

#### R-300-500

```yaml
id: R-300-500
version: 1
status: draft
category: functional
```

The system SHALL widget the frobulator.
"""


def _done_brainstorm() -> dict[str, Any]:
    return {"status": "DONE", "output": {"proposal": "widget the frobulator"}}


def _done_spec_referencing_existing() -> dict[str, Any]:
    # The agent "writes" spec entities — in the e2e we pretend the agent
    # already asked C5 to confirm existing ones, returns DONE.
    return {
        "status": "DONE",
        "output": {"entities": ["R-300-500"]},
    }


def _done_plan() -> dict[str, Any]:
    return {
        "status": "DONE",
        "output": {"steps": [{"id": 1, "description": "implement widget"}]},
    }


def _done_generate_gate_b_passes() -> dict[str, Any]:
    return {
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


def _done_review_gate_c_passes() -> dict[str, Any]:
    past = datetime.now(UTC) - timedelta(seconds=5)
    now = datetime.now(UTC)
    return {
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


@pytest.mark.asyncio
async def test_golden_path_full_pipeline(platform_stack: PlatformStack) -> None:
    """End-to-end: C2 → C3 → C5 seeding → C4 pipeline → COMPLETED."""
    stack = platform_stack
    project_id = "e2e-demo"
    session_id = "sess-golden"
    user_id = "alice@demo"
    tenant_id = "tenant-a"

    # -------------------------------------------------------------
    # 1) C2: issue a token (none mode — no user creation required)
    # -------------------------------------------------------------
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.c2_app), base_url="http://e2e"
    ) as c2:
        token_resp = await c2.post(
            "/auth/login",
            json={"username": "ignored", "password": "ignored"},
        )
        assert token_resp.status_code == 200, token_resp.text
        token_body = token_resp.json()
        assert token_body["token_type"] == "bearer"
        assert token_body["access_token"]

    # -------------------------------------------------------------
    # 2) C3: create a conversation
    # -------------------------------------------------------------
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.c3_app), base_url="http://e2e"
    ) as c3:
        conv_resp = await c3.post(
            "/api/v1/conversations",
            json={"title": "E2e golden run", "project_id": project_id},
            headers=_c3_headers(user_id),
        )
        assert conv_resp.status_code == 201, conv_resp.text
        conv_id = conv_resp.json()["conversation"]["id"]
        assert conv_id

    # -------------------------------------------------------------
    # 3) C5: seed a document with an entity the pipeline will reference
    # -------------------------------------------------------------
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.c5_app), base_url="http://e2e"
    ) as c5:
        create = await c5.post(
            f"/api/v1/projects/{project_id}/requirements/documents",
            json={"slug": "300-SPEC-DEMO"},
            headers=_c5_headers(user_id),
        )
        assert create.status_code == 201, create.text
        put = await c5.put(
            f"/api/v1/projects/{project_id}/requirements/documents/300-SPEC-DEMO",
            json={"content": _SPEC_DOC},
            headers={
                **_c5_headers(user_id),
                "If-Match": '"300-SPEC-DEMO@v1"',
            },
        )
        assert put.status_code == 200, put.text
        entity = await c5.get(
            f"/api/v1/projects/{project_id}/requirements/entities/R-300-500",
            headers=_c5_headers(user_id),
        )
        assert entity.status_code == 200
        assert entity.json()["entity_id"] == "R-300-500"

    # -------------------------------------------------------------
    # 4) C4: script the LLM responses for the full pipeline
    # -------------------------------------------------------------
    stack.scripted_llm.enqueue(_done_brainstorm())
    stack.scripted_llm.enqueue(_done_spec_referencing_existing())
    stack.scripted_llm.enqueue(_done_plan())
    stack.scripted_llm.enqueue(_done_generate_gate_b_passes())
    stack.scripted_llm.enqueue(_done_review_gate_c_passes())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.c4_app), base_url="http://e2e"
    ) as c4:
        run = await c4.post(
            "/api/v1/orchestrator/runs",
            json={
                "project_id": project_id,
                "session_id": session_id,
                "initial_prompt": "build the frobulator widget",
                "domain": "code",
            },
            headers=_c4_headers(user_id, tenant_id),
        )
        assert run.status_code == 201, run.text
        run_body = run.json()
        assert run_body["current_phase"] == Phase.PLAN.value

        approve = await c4.post(
            f"/api/v1/orchestrator/runs/{run_body['run_id']}/feedback",
            json={"phase": Phase.PLAN.value, "approved": True},
            headers=_c4_headers(user_id, tenant_id),
        )
        assert approve.status_code == 200, approve.text
        final = approve.json()

    # -------------------------------------------------------------
    # 5) Assertions — the run completed end-to-end
    # -------------------------------------------------------------
    assert final["status"] == RunStatus.COMPLETED.value
    assert final["project_id"] == project_id
    assert final["tenant_id"] == tenant_id
    # Every scripted LLM response was consumed (5 phases invoked)
    assert len(stack.scripted_llm.calls_seen) == 5

    # The MinIO root is addressable (sanity — C4 stored its prefix)
    assert final["minio_root"].startswith("c4-runs/")


@pytest.mark.asyncio
async def test_cross_tenant_entity_read_forbidden(platform_stack: PlatformStack) -> None:
    """Tenant isolation: Bob reading Alice's project's entity without
    being on the tenant SHALL NOT succeed. Using C3 as the proxy here —
    its rbac is strict on owner_id, matching the broader platform
    contract on forward-auth headers.
    """
    stack = platform_stack
    alice_headers = _c3_headers("alice@demo")
    bob_headers = _c3_headers("bob@stranger")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.c3_app), base_url="http://e2e"
    ) as c3:
        created = await c3.post(
            "/api/v1/conversations",
            json={"title": "private", "project_id": "e2e-priv"},
            headers=alice_headers,
        )
        assert created.status_code == 201
        conv_id = created.json()["conversation"]["id"]

        stranger = await c3.get(
            f"/api/v1/conversations/{conv_id}", headers=bob_headers
        )
    assert stranger.status_code == 403
