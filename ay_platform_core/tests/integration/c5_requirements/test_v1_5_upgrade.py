# =============================================================================
# File: test_v1_5_upgrade.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_v1_5_upgrade.py
# Description: Integration tests covering the v1.5 upgrade to C5:
#              - reindex (R-300-070..073) end-to-end,
#              - reconciliation tick (R-300-063),
#              - Markdown export streaming (R-300-084/086).
#              Exercises MinIO + ArangoDB together per write-through.
# =============================================================================

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c5_requirements.models import ReindexJobStatus
from ay_platform_core.c5_requirements.service import (
    ReconcileReport,
    RequirementsService,
)
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

pytestmark = pytest.mark.integration

_ACTOR_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_owner,admin",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


_DOC_A = """---
document: 300-SPEC-A
version: 1
path: projects/demo/requirements/300-SPEC-A.md
language: en
status: draft
---

# Spec A

#### R-300-500

```yaml
id: R-300-500
version: 1
status: draft
category: functional
```

Body for requirement A.
"""

_DOC_B = """---
document: 300-SPEC-B
version: 1
path: projects/demo/requirements/300-SPEC-B.md
language: en
status: draft
---

# Spec B

#### R-300-501

```yaml
id: R-300-501
version: 1
status: draft
category: architecture
```

Body for requirement B.
"""


async def _seed(client: httpx.AsyncClient, slug: str, content: str) -> None:
    resp = await client.post(
        "/api/v1/projects/demo/requirements/documents",
        json={"slug": slug},
        headers=_ACTOR_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    resp = await client.put(
        f"/api/v1/projects/demo/requirements/documents/{slug}",
        json={"content": content},
        headers={**_ACTOR_HEADERS, "If-Match": f'"{slug}@v1"'},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reindex_processes_all_documents(
    c5_app: FastAPI, c5_service: RequirementsService
) -> None:
    async with _client(c5_app) as client:
        await _seed(client, "300-SPEC-A", _DOC_A)
        await _seed(client, "300-SPEC-B", _DOC_B)

        resp = await client.post(
            "/api/v1/projects/demo/requirements/reindex", headers=_ACTOR_HEADERS
        )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # Wait for the background task to finish (with timeout + explicit error).
    await c5_service.aclose()

    # Poll the job status — the background task may still be flushing its
    # final upsert at the moment aclose() returns (the done-callback
    # removes it from the set before its last await resolves).
    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        async with _client(c5_app) as client:
            resp = await client.get(
                f"/api/v1/projects/demo/requirements/reindex/{job_id}",
                headers=_ACTOR_HEADERS,
            )
        assert resp.status_code == 200
        job = resp.json()
        if job["status"] in {
            ReindexJobStatus.COMPLETED.value,
            ReindexJobStatus.FAILED.value,
        }:
            break
        await asyncio.sleep(0.1)

    assert job["status"] == ReindexJobStatus.COMPLETED.value, job
    assert job["processed_entities"] == 2


@pytest.mark.asyncio
async def test_reindex_idempotent(
    c5_app: FastAPI, c5_service: RequirementsService
) -> None:
    async with _client(c5_app) as client:
        await _seed(client, "300-SPEC-A", _DOC_A)
        first = await client.post(
            "/api/v1/projects/demo/requirements/reindex", headers=_ACTOR_HEADERS
        )
        second = await client.post(
            "/api/v1/projects/demo/requirements/reindex", headers=_ACTOR_HEADERS
        )
    # R-300-072: a second trigger returns the existing job id while still running.
    assert first.status_code == 202
    assert second.status_code == 202
    # If the first job already completed before the second call (fast ASGI loop),
    # the service legitimately starts a fresh job — accept both flows.
    if first.json()["status"] in ("pending", "running"):
        assert first.json()["job_id"] == second.json()["job_id"]
    await c5_service.aclose()


@pytest.mark.asyncio
async def test_reindex_requires_admin(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/reindex",
            headers={"X-User-Id": "bob", "X-User-Roles": "project_editor"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_detects_missing_in_index(
    c5_app: FastAPI,
    c5_storage: RequirementsStorage,
) -> None:
    async with _client(c5_app) as client:
        await _seed(client, "300-SPEC-A", _DOC_A)

        # Inject a rogue document directly into MinIO that ArangoDB does
        # not know about. The reconciler SHALL detect and index it.
        rogue_path = "projects/demo/requirements/300-SPEC-ROGUE.md"
        rogue_body = _DOC_A.replace("300-SPEC-A", "300-SPEC-ROGUE").replace(
            "R-300-500", "R-300-700"
        )
        await c5_storage.put_document(rogue_path, rogue_body.encode("utf-8"))

        resp = await client.post(
            "/api/v1/projects/demo/requirements/reconcile", headers=_ACTOR_HEADERS
        )
    assert resp.status_code == 200
    report = resp.json()
    assert report["missing_in_index"] >= 1
    assert report["repaired"] >= 1

    async with _client(c5_app) as client:
        resp = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-300-700",
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_reconcile_detects_stale_index(
    c5_app: FastAPI,
    c5_storage: RequirementsStorage,
    c5_service: RequirementsService,
) -> None:
    async with _client(c5_app) as client:
        await _seed(client, "300-SPEC-A", _DOC_A)

        # Tamper the document on MinIO without going through the API — the
        # index's content_hash no longer matches the source.
        new_body = _DOC_A.replace(
            "Body for requirement A.",
            "Body for requirement A (edited outside API).",
        )
        await c5_storage.put_document(
            "projects/demo/requirements/300-SPEC-A.md", new_body.encode("utf-8")
        )

    report: ReconcileReport = await c5_service.reconcile_tick("demo")
    assert report.stale_in_index >= 1
    assert report.repaired >= 1


@pytest.mark.asyncio
async def test_reconcile_requires_admin(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/reconcile",
            headers={"X-User-Id": "bob", "X-User-Roles": "project_editor"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_markdown_contains_all_documents(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client, "300-SPEC-A", _DOC_A)
        await _seed(client, "300-SPEC-B", _DOC_B)

        resp = await client.get(
            "/api/v1/projects/demo/requirements/export",
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    body = resp.text
    # Banners separate documents per our export convention.
    assert "300-SPEC-A.md ===" in body
    assert "300-SPEC-B.md ===" in body
    # Entity bodies are preserved
    assert "R-300-500" in body
    assert "R-300-501" in body


@pytest.mark.asyncio
async def test_export_rejects_unsupported_format(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.get(
            "/api/v1/projects/demo/requirements/export?format=reqif",
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 501
    assert "reqif" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_export_rejects_point_in_time(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.get(
            "/api/v1/projects/demo/requirements/export?at=2026-04-23T00:00:00Z",
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 501
    assert "R-300-085" in resp.json()["detail"]


# Satisfy unused-import lint — asyncio is imported for typing reasons.
_ = asyncio
