# =============================================================================
# File: test_source_diagnostics_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_source_diagnostics_flow.py
# Description: Integration test for the source-diagnostics endpoint
#              (`GET /api/v1/memory/projects/{p}/sources/{sid}/diagnostics`).
#              Ingests a JSON source (real chunking + deterministic embeddings
#              against a live ArangoDB) then GETs /diagnostics and asserts the
#              business payload: parse status, chunk_count matching the per-
#              chunk diagnostics, MinIO storage paths, and that each chunk row
#              carries a token count + embedding flag. This is the functional
#              tier counterpart to the unit smoke test — the auth-matrix only
#              covers the authorization dimension, not the observable payload.
#
# @relation validates:R-400-070
# =============================================================================

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.models import ParseStatus, SourceIngestRequest
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-diag"
_PROJECT = "project-diag"
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


async def test_diagnostics_reports_chunks_and_storage(
    c7_upload_app: FastAPI,
) -> None:
    """Ingest a multi-sentence source, then GET /diagnostics and assert
    the payload mirrors the persisted state: INDEXED status, a chunk_count
    equal to the number of per-chunk diagnostics, populated storage paths,
    and a token_count on each chunk."""
    source_id = f"src-{uuid.uuid4().hex[:6]}"
    await _service(c7_upload_app).ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=_PROJECT,
            mime_type="text/markdown",
            content=(
                "# Voyager\n\nVoyager 1 launched in 1977. "
                "It is the most distant human-made object. "
                "It crossed the heliopause in 2012."
            ),
            size_bytes=160,
            uploaded_by="alice",
        ),
        tenant_id=_TENANT,
    )

    async with _client(c7_upload_app) as c:
        resp = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/sources/{source_id}/diagnostics",
            headers=_HEADERS,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_id"] == source_id
    assert body["project_id"] == _PROJECT
    assert body["parse_status"] == ParseStatus.INDEXED.value
    assert body["parse_error"] is None

    chunks = body["chunks"]
    assert chunks, "expected at least one chunk diagnostic"
    assert body["chunk_count"] == len(chunks)
    for ch in chunks:
        assert ch["token_count"] >= 0
        assert isinstance(ch["has_embedding"], bool)

    storage = body["storage"]
    assert storage["artifacts_bucket"]
    assert storage["raw_bucket"]


async def test_diagnostics_returns_404_for_unknown_source(
    c7_upload_app: FastAPI,
) -> None:
    async with _client(c7_upload_app) as c:
        resp = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/sources/nope-{uuid.uuid4().hex}/diagnostics",
            headers=_HEADERS,
        )
    assert resp.status_code == 404
