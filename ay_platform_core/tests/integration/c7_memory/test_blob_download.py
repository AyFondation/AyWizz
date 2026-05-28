# =============================================================================
# File: test_blob_download.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_blob_download.py
# Description: Integration tests for the file-download endpoint
#              (`GET /api/v1/memory/projects/{p}/sources/{sid}/blob`)
#              added 2026-04-29 as gap UX #5 (frontend file viewer +
#              download). Three tests:
#                1. Round-trip: upload bytes → GET /blob returns SAME
#                   bytes + correct Content-Type + Content-Disposition.
#                2. 404 when source row exists but blob is missing
#                   (JSON-only ingest path doesn't write to MinIO).
#                3. 404 on unknown source_id (tenant scope check).
#                4. 503 when storage is not wired.
#
# @relation validates:R-400-070
# =============================================================================

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import ParseStatus, SourceIngestRequest
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.service import get_service as c7_get_service

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-blob"
_PROJECT = "project-blob"
_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": _TENANT,
    "X-User-Roles": "project_editor",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


async def _upload_text(
    app: FastAPI, source_id: str, body: bytes, filename: str = "doc.txt",
) -> None:
    """D-020 session 7 — `/sources/upload` removed. The blob endpoint
    serves bytes that C12 (n8n) PUT directly to MinIO during the n8n
    workflow ; we reproduce that exact pattern by reaching into the
    `c7_upload_service` instance attached to `app.dependency_overrides`
    and calling its `_storage.put_source_blob` adapter directly, plus a
    minimal `upsert_source` so `/blob`'s mime_type lookup succeeds.
    """
    service = app.dependency_overrides[c7_get_service]()
    tenant_id = _HEADERS["X-Tenant-Id"]
    await service._storage.put_source_blob(
        tenant_id=tenant_id,
        project_id=_PROJECT,
        source_id=source_id,
        data=body,
        mime_type="text/plain",
    )
    await service._repo.upsert_source({
        "_key": f"{tenant_id}:{_PROJECT}:{source_id}",
        "tenant_id": tenant_id,
        "project_id": _PROJECT,
        "source_id": source_id,
        "minio_raw_path": None,
        "minio_parsed_path": None,
        "minio_chunks_path": None,
        "mime_type": "text/plain",
        "size_bytes": len(body),
        "uploaded_by": _HEADERS["X-User-Id"],
        "uploaded_at": "2026-05-28T00:00:00+00:00",
        "parse_status": ParseStatus.INDEXED.value,
        "parse_error": None,
        "chunk_count": 0,
        "model_id": "deterministic-hash-v1",
        "processing_version": "chunk=512/64;embed=deterministic-hash-v1",
    })


async def test_download_blob_round_trips_uploaded_bytes(
    c7_upload_app: FastAPI,
) -> None:
    """End-to-end: upload bytes via the multipart endpoint, then GET
    /blob and assert the bytes match exactly. The Content-Type SHALL
    match the upload mime_type and Content-Disposition SHALL carry a
    sensible filename (the source_id with a guessed extension)."""
    source_id = f"src-{uuid.uuid4().hex[:6]}"
    body = b"Voyager 1 launched 1977. Distant human-made object in space."
    await _upload_text(c7_upload_app, source_id=source_id, body=body)

    async with _client(c7_upload_app) as c:
        resp = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/sources/{source_id}/blob",
            headers=_HEADERS,
        )

    assert resp.status_code == 200, resp.text
    assert resp.content == body, (
        f"blob round-trip mismatch — sent {len(body)}, got "
        f"{len(resp.content)}"
    )
    assert resp.headers["content-type"].startswith("text/plain")
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd
    assert source_id in cd


async def test_download_returns_404_when_source_has_no_blob(
    c7_upload_app: FastAPI,
) -> None:
    """Sources ingested via the JSON `POST /sources` path don't write
    to MinIO. Hitting /blob on those SHALL return 404 with a
    descriptive detail rather than 500."""
    source_id = f"src-jsononly-{uuid.uuid4().hex[:6]}"

    # Ingest JSON-only via the service directly (skips multipart, so
    # no blob is written).
    service: MemoryService = c7_upload_app.state.requirements_service \
        if hasattr(c7_upload_app.state, "requirements_service") \
        else c7_upload_app.dependency_overrides.get(c7_get_service, lambda: None)()
    if service is None:
        # Fallback: read it from app.state where the fixture sets it.
        service = c7_upload_app.state.memory_service
    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=_PROJECT,
            mime_type="text/plain",
            content="some content but no blob",
            size_bytes=24,
            uploaded_by="alice",
        ),
        tenant_id=_TENANT,
    )

    async with _client(c7_upload_app) as c:
        resp = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/sources/{source_id}/blob",
            headers=_HEADERS,
        )
    assert resp.status_code == 404
    detail = resp.json().get("detail", "")
    # Accept either of the two 404 reasons (row missing OR blob missing)
    # but prefer the blob-missing one since the row IS present.
    assert "blob" in detail.lower() or "not found" in detail.lower()


async def test_download_returns_404_for_unknown_source(
    c7_upload_app: FastAPI,
) -> None:
    async with _client(c7_upload_app) as c:
        resp = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/sources/nope-{uuid.uuid4().hex}/blob",
            headers=_HEADERS,
        )
    assert resp.status_code == 404


async def test_download_returns_503_when_storage_not_wired(
    c7_repo: MemoryRepository,
) -> None:
    """A service constructed WITHOUT a MinIO storage SHALL 503 on
    download — same convention as the upload endpoint."""
    embedder = DeterministicHashEmbedder(dimension=64)
    service = MemoryService(
        config=MemoryConfig(embedding_dimension=embedder.dimension),
        repo=c7_repo,
        embedder=embedder,
        # No `storage` injected — download is unavailable.
    )
    app = FastAPI()
    app.include_router(c7_router)
    app.dependency_overrides[c7_get_service] = lambda: service

    async with _client(app) as c:
        resp = await c.get(
            f"/api/v1/memory/projects/{_PROJECT}/sources/anything/blob",
            headers=_HEADERS,
        )
    assert resp.status_code == 503
    assert "blob storage not configured" in resp.json()["detail"]
