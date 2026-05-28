# =============================================================================
# File: test_run_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c6_validation/test_run_flow.py
# Description: Integration tests for the C6 validation run lifecycle. Uses
#              REAL ArangoDB (findings/runs collections) and REAL MinIO
#              (snapshot bucket). Exercises:
#              - full pipeline for a CLEAN project (stubs only emit info)
#              - full pipeline for a DIRTY project (various blocking
#                findings)
#              - HTTP surface via httpx ASGI transport + forward-auth
#                headers
#              - snapshot persistence + round-trip
#              - truncation guard
#              - disable-check config knob
# =============================================================================

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    RunStatus,
    RunTriggerRequest,
    Severity,
)
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)

pytestmark = pytest.mark.integration


_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_editor,project_owner",
}


def _req(entity_id: str, *, status: str = "approved", type_: str = "R") -> dict[str, Any]:
    return {"entity_id": entity_id, "status": status, "type": type_}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# ---------------------------------------------------------------------------
# Service-level tests (preferred for coverage of the orchestration layer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_project_completes_with_only_info_findings(
    c6_service: ValidationService,
) -> None:
    """A clean corpus: one requirement implemented by one module, validated
    by one test. Blocking findings should be zero; the 4 stubs contribute
    info findings.
    """
    requirements = [_req("R-100-001")]
    artifacts = [
        CodeArtifact(
            path="src/impl.py",
            content="# @relation implements:R-100-001\n",
        ),
        CodeArtifact(
            path="tests/unit/test_impl.py",
            content="# @relation validates:R-100-001\n",
            is_test=True,
        ),
    ]
    payload = RunTriggerRequest(
        domain="code", project_id="demo", check_ids=[]
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=requirements, artifacts=artifacts
    )
    assert run.status == RunStatus.COMPLETED
    assert run.findings_count.blocking == 0
    # 1 remaining stub check (#3 interface-signature-drift) emits a single
    # info finding. The other 8 checks are real and pass silently on a clean
    # corpus (#8 data-model-drift, de-stubbed under D-017, is opt-in and emits
    # nothing without `fields:`/`model_name:` declarations).
    assert run.findings_count.info >= 1
    # A clean run earns a perfect deterministic verdict (D-017 / R-700-031).
    assert run.verdict is not None
    assert run.verdict.score == 1.0


@pytest.mark.asyncio
async def test_dirty_project_emits_expected_blocking_findings(
    c6_service: ValidationService,
) -> None:
    """Dirty corpus scenarios, one per category."""
    requirements = [
        _req("R-100-001"),  # no impl → req-without-code
        _req("R-100-002", status="deprecated"),  # deprecated target → obsolete
    ]
    artifacts = [
        CodeArtifact(path="src/empty.py", content="def bare(): pass\n"),
        CodeArtifact(
            path="src/impl.py",
            content="# @relation implements:R-100-002\n",
        ),
        CodeArtifact(
            path="tests/unit/test_orphan.py",
            content="def test_nothing(): assert True\n",
            is_test=True,
        ),
    ]
    payload = RunTriggerRequest(domain="code", project_id="demo")
    run = await c6_service.execute_run_sync(
        payload, requirements=requirements, artifacts=artifacts
    )
    assert run.status == RunStatus.COMPLETED
    assert run.findings_count.blocking >= 4  # at least one per violated check


@pytest.mark.asyncio
async def test_marker_syntax_error_surfaces_as_blocking(
    c6_service: ValidationService,
) -> None:
    artifacts = [
        CodeArtifact(
            path="src/bad.py",
            content="# @relation implements:BAD-ID\n",
        )
    ]
    payload = RunTriggerRequest(
        domain="code", project_id="demo", check_ids=["req-without-code"]
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=[], artifacts=artifacts
    )
    # marker-syntax always runs, regardless of check_ids filter.
    assert run.findings_count.blocking >= 1


@pytest.mark.asyncio
async def test_snapshot_is_written_and_readable(
    c6_service: ValidationService,
    c6_snapshot_store: ValidationSnapshotStorage,
) -> None:
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["interface-signature-drift"],  # persistent stub
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=[], artifacts=[]
    )
    assert run.snapshot_uri is not None
    raw = await c6_snapshot_store.get_snapshot(run.snapshot_uri)
    body = json.loads(raw)
    assert body["run"]["run_id"] == run.run_id
    assert isinstance(body["findings"], list)


@pytest.mark.asyncio
async def test_unknown_domain_returns_404(
    c6_service: ValidationService,
) -> None:
    from fastapi import HTTPException  # noqa: PLC0415

    payload = RunTriggerRequest(domain="presentation", project_id="demo")
    with pytest.raises(HTTPException) as exc_info:
        await c6_service.execute_run_sync(
            payload, requirements=[], artifacts=[]
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_check_is_skipped(
    c6_service: ValidationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("C6_CHECK_REQ_WITHOUT_CODE_ENABLED", "false")
    requirements = [_req("R-100-001")]
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["req-without-code"],
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=requirements, artifacts=[]
    )
    assert run.status == RunStatus.COMPLETED
    # One info finding "req-without-code:disabled", zero blockings.
    assert run.findings_count.blocking == 0
    assert run.findings_count.info >= 1


@pytest.mark.asyncio
async def test_truncation_guard(
    c6_service: ValidationService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Lower the cap on this service instance and trigger a run that would
    # exceed it: 150 bare modules → 150 blockings from code-without-requirement.
    c6_service._config.max_findings_per_run = 50
    artifacts = [
        CodeArtifact(path=f"src/mod_{i}.py", content="def x(): pass\n")
        for i in range(150)
    ]
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["code-without-requirement"],
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=[], artifacts=artifacts
    )
    assert run.status == RunStatus.COMPLETED
    # 50 blockings + 1 info (truncation notice).
    total = (
        run.findings_count.blocking
        + run.findings_count.advisory
        + run.findings_count.info
    )
    assert total == 51


# ---------------------------------------------------------------------------
# HTTP surface tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugins_endpoint_lists_code_plugin(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.get(
            "/api/v1/validation/plugins", headers=_HEADERS
        )
    assert resp.status_code == 200
    body = resp.json()
    names = {p["name"] for p in body}
    assert "builtin-code" in names


@pytest.mark.asyncio
async def test_domains_endpoint(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.get(
            "/api/v1/validation/domains", headers=_HEADERS
        )
    assert resp.status_code == 200
    assert "code" in resp.json()["domains"]


@pytest.mark.asyncio
async def test_trigger_run_returns_202_and_findings_accessible(
    c6_app: FastAPI,
) -> None:
    async with _client(c6_app) as client:
        resp = await client.post(
            "/api/v1/validation/runs",
            json={
                "domain": "code",
                "project_id": "demo",
                # interface-signature-drift is still a stub in v1; it always
                # emits one info finding regardless of the corpus, which lets
                # us assert on a deterministic minimal result.
                "check_ids": ["interface-signature-drift"],
                "requirements": [],
                "artifacts": [],
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]

        # Poll until completion (bounded; tests use the sync-exec path when
        # possible, this one exercises trigger_run + background task).
        completed = False
        for _ in range(40):
            detail = await client.get(
                f"/api/v1/validation/runs/{run_id}", headers=_HEADERS
            )
            if detail.status_code == 200 and detail.json()["status"] == "completed":
                completed = True
                break
            import asyncio  # noqa: PLC0415 — local-only
            await asyncio.sleep(0.05)
        assert completed, "run never reached COMPLETED"

        findings = await client.get(
            f"/api/v1/validation/runs/{run_id}/findings",
            headers=_HEADERS,
        )
    assert findings.status_code == 200
    body = findings.json()
    assert body["run_id"] == run_id
    # The interface-signature-drift stub always emits at least one info finding.
    assert body["total"] >= 1
    assert body["items"][0]["check_id"] == "interface-signature-drift"


@pytest.mark.asyncio
async def test_trigger_run_unknown_domain_returns_404(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.post(
            "/api/v1/validation/runs",
            json={"domain": "presentation", "project_id": "demo"},
            headers=_HEADERS,
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run_404(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.get(
            f"/api/v1/validation/runs/{uuid.uuid4()}", headers=_HEADERS
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_findings_requires_authentication(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.get(
            "/api/v1/validation/plugins"
        )  # no X-User-Id header
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trigger_requires_editor_role(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.post(
            "/api/v1/validation/runs",
            json={"domain": "code", "project_id": "demo"},
            headers={"X-User-Id": "alice", "X-User-Roles": "viewer"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_findings_pagination_params_validated(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.get(
            f"/api/v1/validation/runs/{uuid.uuid4()}/findings?limit=0",
            headers=_HEADERS,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_finding_returns_404(c6_app: FastAPI) -> None:
    async with _client(c6_app) as client:
        resp = await client.get(
            f"/api/v1/validation/findings/{uuid.uuid4()}",
            headers=_HEADERS,
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_individual_finding_round_trips(
    c6_app: FastAPI, c6_service: ValidationService
) -> None:
    requirements = [_req("R-100-001")]
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["req-without-code"],
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=requirements, artifacts=[]
    )
    async with _client(c6_app) as client:
        listing = await client.get(
            f"/api/v1/validation/runs/{run.run_id}/findings",
            headers=_HEADERS,
        )
        finding_id = listing.json()["items"][0]["finding_id"]
        detail = await client.get(
            f"/api/v1/validation/findings/{finding_id}",
            headers=_HEADERS,
        )
    assert detail.status_code == 200
    body = detail.json()
    assert body["finding_id"] == finding_id
    assert body["severity"] == Severity.BLOCKING.value
