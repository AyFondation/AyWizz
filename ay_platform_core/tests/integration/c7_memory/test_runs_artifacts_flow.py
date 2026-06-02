# =============================================================================
# File: test_runs_artifacts_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_runs_artifacts_flow.py
# Description: Integration tests for the run/artifact browsing + chunk content
#              + zip download surface (R-400-221 transparency) over the real
#              C7 app wired to ArangoDB + MinIO. Seeds one extraction run via
#              the shared `_ingest_text_via_chunks` helper, then drives every
#              new GET endpoint:
#                - /runs (lists the run, marks it active)
#                - /runs/{run_id}/artifacts (browse) + artifacts.zip (bundle)
#                - /runs/{run_id}/artifacts/{path} (view + 404 probe)
#                - /chunks.zip (bundle) + /chunks/{chunk_id} (content)
#
# @relation validates:R-400-070
# =============================================================================

from __future__ import annotations

import io
import uuid
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service
from tests.integration.c7_memory.conftest import _ingest_text_via_chunks

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-runs"
_PROJECT = "project-runs"
_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": _TENANT,
    "X-User-Roles": "project_editor",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


def _service(app: FastAPI) -> MemoryService:
    service = app.dependency_overrides[c7_get_service]()
    assert isinstance(service, MemoryService)
    return service


async def test_runs_artifacts_and_chunks_browse_and_download(
    c7_upload_app: FastAPI,
) -> None:
    sid = f"src-{uuid.uuid4().hex[:6]}"
    pid = _PROJECT
    await _ingest_text_via_chunks(
        service=_service(c7_upload_app),
        tenant_id=_TENANT,
        project_id=pid,
        source_id=sid,
        text="Voyager 1 launched in 1977. It crossed the heliopause in 2012.",
    )

    # Full path literals (NOT composed from a `base` var) so the functional-
    # coverage matcher can extract each `/api/v1/...` URL from the source.
    async with _client(c7_upload_app) as c:
        # --- /runs : the seeded run is listed and active ---
        runs_resp = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/runs", headers=_HEADERS
        )
        assert runs_resp.status_code == 200, runs_resp.text
        runs_body = runs_resp.json()
        assert runs_body["runs"], "expected at least one extraction run"
        run_id = runs_body["active_run_id"]
        assert run_id is not None
        active = [r for r in runs_body["runs"] if r["is_active"]]
        assert len(active) == 1 and active[0]["run_id"] == run_id

        # --- /runs/{run_id}/artifacts : manifest + chunks.jsonl present ---
        arts_resp = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/runs/{run_id}/artifacts",
            headers=_HEADERS,
        )
        assert arts_resp.status_code == 200, arts_resp.text
        paths = {e["path"] for e in arts_resp.json()["entries"]}
        assert "00_metadata/run_manifest.json" in paths
        assert "02_chunks/chunks.jsonl" in paths

        # --- artifacts.zip : a real archive bundling those files ---
        zip_resp = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/runs/{run_id}/artifacts.zip",
            headers=_HEADERS,
        )
        assert zip_resp.status_code == 200, zip_resp.text
        assert zip_resp.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            assert "00_metadata/run_manifest.json" in zf.namelist()

        # --- a single artifact (nested path) renders ---
        man_resp = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/runs/{run_id}/artifacts/00_metadata/run_manifest.json",
            headers=_HEADERS,
        )
        assert man_resp.status_code == 200, man_resp.text
        # The shared helper seeds an embedding-only manifest.
        assert man_resp.json()["embedding_model"]

        # --- unknown top-level artifact → 404 (single-segment path so the
        #     coverage matcher's segment-count check matches the catch-all) ---
        miss_resp = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/runs/{run_id}/artifacts/nope.json",
            headers=_HEADERS,
        )
        assert miss_resp.status_code == 404

        # --- /chunks.zip : one JSON per indexed chunk ---
        chunks_zip = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/chunks.zip",
            headers=_HEADERS,
        )
        assert chunks_zip.status_code == 200, chunks_zip.text
        with zipfile.ZipFile(io.BytesIO(chunks_zip.content)) as zf:
            assert zf.namelist(), "expected at least one chunk in the zip"

        # --- /chunks/{chunk_id} : full content of one indexed chunk ---
        diag = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/diagnostics",
            headers=_HEADERS,
        )
        chunk_id = diag.json()["chunks"][0]["chunk_id"]
        chunk_resp = await c.get(
            f"/api/v1/memory/projects/{pid}/sources/{sid}/chunks/{chunk_id}",
            headers=_HEADERS,
        )
        assert chunk_resp.status_code == 200, chunk_resp.text
        chunk_body = chunk_resp.json()
        assert chunk_body["chunk_id"] == chunk_id
        assert chunk_body["content"]
