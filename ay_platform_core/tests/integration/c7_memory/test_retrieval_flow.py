# =============================================================================
# File: test_retrieval_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_retrieval_flow.py
# Description: Integration tests for the C7 Memory Service. Uses REAL
#              ArangoDB via testcontainers and the deterministic embedder.
#              Exercises: source ingestion (parse + chunk + embed + index),
#              federated retrieval, entity embedding + versioning,
#              quota, RBAC, source deletion cascades.
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.base import EmbeddingProvider
from ay_platform_core.c7_memory.models import IndexKind, RetrievalRequest
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = pytest.mark.integration

_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-a",
    "X-User-Roles": "project_editor,project_owner,admin",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Source ingestion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_text_source_creates_chunks_and_allows_retrieval(
    c7_app: FastAPI,
) -> None:
    async with _client(c7_app) as client:
        ingest = await client.post(
            "/api/v1/memory/projects/p1/sources",
            json={
                "source_id": "s1",
                "project_id": "p1",
                "mime_type": "text/plain",
                "content": (
                    "The frobulator widget processes thimble data streams "
                    "and emits structured events to the knowledge graph."
                ),
                "size_bytes": 120,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
    assert ingest.status_code == 201, ingest.text
    body = ingest.json()
    assert body["source_id"] == "s1"
    assert body["parse_status"] == "indexed"
    assert body["chunk_count"] >= 1

    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "frobulator widget",
                "indexes": ["external_sources"],
                "top_k": 5,
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["hits"]) >= 1
    # Top hit must carry provenance back to our source.
    assert body["hits"][0]["source_id"] == "s1"
    assert body["hits"][0]["score"] > 0.0


@pytest.mark.asyncio
async def test_ingest_markdown_strips_frontmatter(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        ingest = await client.post(
            "/api/v1/memory/projects/p1/sources",
            json={
                "source_id": "s-md",
                "project_id": "p1",
                "mime_type": "text/markdown",
                "content": (
                    "---\n"
                    "title: sample\n"
                    "---\n"
                    "# Heading\n\nThe quick brown fox jumps over the lazy dog."
                ),
                "size_bytes": 80,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
    assert ingest.status_code == 201

    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "sample",
                "indexes": ["external_sources"],
                "top_k": 5,
            },
            headers=_HEADERS,
        )
    # The frontmatter was stripped — "sample" should NOT appear in any chunk.
    for hit in resp.json()["hits"]:
        assert "title: sample" not in hit["content"]


@pytest.mark.asyncio
async def test_pdf_ingest_via_json_payload_returns_422_for_corrupt_bytes(
    c7_app: FastAPI,
) -> None:
    """Phase B activated the PDF parser. Sending an obvious non-PDF
    payload through the JSON ingest path now yields 422 (parse failure)
    rather than the old 501 (not implemented). The full upload pipeline
    (multipart) is exercised by `test_upload_pipeline.py`."""
    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/projects/p1/sources",
            json={
                "source_id": "s-pdf",
                "project_id": "p1",
                "mime_type": "application/pdf",
                "content": "%PDF-1.4 not really a PDF",
                "size_bytes": 24,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 422
    assert "invalid PDF" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Entity embedding (requirements index) + history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_embed_then_retrieve_requirements(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        embed = await client.post(
            "/api/v1/memory/entities/embed",
            json={
                "project_id": "p1",
                "entity_id": "R-300-500",
                "entity_version": 1,
                "content": "The system SHALL greet the user on login.",
                "metadata": {"category": "functional"},
            },
            headers=_HEADERS,
        )
    assert embed.status_code == 201
    assert embed.json()["entity_id"] == "R-300-500"
    assert embed.json()["status"] == "active"

    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "greet the user",
                "indexes": ["requirements"],
                "top_k": 5,
            },
            headers=_HEADERS,
        )
    body = resp.json()
    assert len(body["hits"]) == 1
    assert body["hits"][0]["entity_id"] == "R-300-500"
    assert body["hits"][0]["entity_version"] == 1


@pytest.mark.asyncio
async def test_entity_version_upgrade_preserves_history(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        await client.post(
            "/api/v1/memory/entities/embed",
            json={
                "project_id": "p1",
                "entity_id": "R-300-501",
                "entity_version": 1,
                "content": "Alpha version of the requirement.",
            },
            headers=_HEADERS,
        )
        await client.post(
            "/api/v1/memory/entities/embed",
            json={
                "project_id": "p1",
                "entity_id": "R-300-501",
                "entity_version": 2,
                "content": "Beta version, newly revised wording.",
            },
            headers=_HEADERS,
        )
        # Default retrieval: only v2 (active) should appear.
        default_resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "version",
                "indexes": ["requirements"],
                "top_k": 10,
            },
            headers=_HEADERS,
        )
        # With include_history=True, both versions surface.
        history_resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "version",
                "indexes": ["requirements"],
                "top_k": 10,
                "include_history": True,
            },
            headers=_HEADERS,
        )

    default_versions = {
        h["entity_version"] for h in default_resp.json()["hits"]
        if h["entity_id"] == "R-300-501"
    }
    assert default_versions == {2}

    history_versions = {
        h["entity_version"] for h in history_resp.json()["hits"]
        if h["entity_id"] == "R-300-501"
    }
    assert history_versions == {1, 2}


# ---------------------------------------------------------------------------
# Federated retrieval — both indexes with weights
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_federated_retrieval_merges_indexes(
    c7_app: FastAPI,
    c7_repo: MemoryRepository,
    c7_embedder: EmbeddingProvider,
    c7_config: MemoryConfig,
) -> None:
    async with _client(c7_app) as client:
        # External source
        await client.post(
            "/api/v1/memory/projects/p1/sources",
            json={
                "source_id": "s-fed",
                "project_id": "p1",
                "mime_type": "text/plain",
                "content": "Shared keyword frobulator appears in an external source.",
                "size_bytes": 60,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
        # Requirements entity
        await client.post(
            "/api/v1/memory/entities/embed",
            json={
                "project_id": "p1",
                "entity_id": "R-300-900",
                "entity_version": 1,
                "content": "Shared keyword frobulator appears in a requirement.",
            },
            headers=_HEADERS,
        )
        resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "frobulator",
                "indexes": ["external_sources", "requirements"],
                "top_k": 10,
                "weights": {"requirements": 2.0, "external_sources": 1.0},
            },
            headers=_HEADERS,
        )
    body = resp.json()
    indexes = {h["index"] for h in body["hits"]}
    # Federated MERGE (the test's purpose) holds under the default hybrid mode:
    # both indexes are represented in the fused results.
    assert "requirements" in indexes
    assert "external_sources" in indexes

    # Index `weights` bias the DENSE arm specifically. Under hybrid (R-400-202)
    # the BM25 arm is unweighted and RRF fuses both, so the 2x weight no longer
    # deterministically owns the top slot. Validate the weight effect in DENSE
    # mode (where it is the ranking authority) over the same data.
    dense_svc = MemoryService(
        config=c7_config.model_copy(update={"retrieval_mode": "dense"}),
        repo=c7_repo,
        embedder=c7_embedder,
    )
    dense = await dense_svc.retrieve(
        RetrievalRequest(
            project_id="p1",
            query="frobulator",
            indexes=[IndexKind.EXTERNAL_SOURCES, IndexKind.REQUIREMENTS],
            top_k=10,
            weights={IndexKind.REQUIREMENTS: 2.0, IndexKind.EXTERNAL_SOURCES: 1.0},
        ),
        tenant_id="tenant-a",
    )
    assert dense.hits[0].index is IndexKind.REQUIREMENTS


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_x_user_id_returns_401(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "anything",
                "indexes": ["requirements"],
            },
            headers={"X-Tenant-Id": "t"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cross_tenant_source_hidden(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        # Alice (tenant-a) creates a source
        await client.post(
            "/api/v1/memory/projects/p-private/sources",
            json={
                "source_id": "s-priv",
                "project_id": "p-private",
                "mime_type": "text/plain",
                "content": "Private tenant content.",
                "size_bytes": 30,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
        # Bob (tenant-b) tries to read it — 404 per R-400-071.
        bob = await client.get(
            "/api/v1/memory/projects/p-private/sources/s-priv",
            headers={
                "X-User-Id": "bob",
                "X-Tenant-Id": "tenant-b",
                "X-User-Roles": "project_editor",
            },
        )
    assert bob.status_code == 404


@pytest.mark.asyncio
async def test_delete_source_requires_owner(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        await client.post(
            "/api/v1/memory/projects/p1/sources",
            json={
                "source_id": "s-del",
                "project_id": "p1",
                "mime_type": "text/plain",
                "content": "Deletable content.",
                "size_bytes": 20,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
        # project_editor alone cannot delete
        denied = await client.delete(
            "/api/v1/memory/projects/p1/sources/s-del",
            headers={
                "X-User-Id": "eve",
                "X-Tenant-Id": "tenant-a",
                "X-User-Roles": "project_editor",
            },
        )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_delete_source_cascades_to_chunks(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        await client.post(
            "/api/v1/memory/projects/p1/sources",
            json={
                "source_id": "s-cascade",
                "project_id": "p1",
                "mime_type": "text/plain",
                "content": "Unique cascade token appears here.",
                "size_bytes": 30,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
        # Confirm retrieval finds it
        before = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "cascade token",
                "indexes": ["external_sources"],
                "top_k": 5,
            },
            headers=_HEADERS,
        )
        assert any(h["source_id"] == "s-cascade" for h in before.json()["hits"])

        # Delete
        resp = await client.delete(
            "/api/v1/memory/projects/p1/sources/s-cascade",
            headers=_HEADERS,
        )
        assert resp.status_code == 204

        # Retrieval no longer returns the source's chunks
        after = await client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "p1",
                "query": "cascade token",
                "indexes": ["external_sources"],
                "top_k": 5,
            },
            headers=_HEADERS,
        )
    assert not any(h["source_id"] == "s-cascade" for h in after.json()["hits"])


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_reports_usage(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        await client.post(
            "/api/v1/memory/projects/p-quota/sources",
            json={
                "source_id": "s-qa",
                "project_id": "p-quota",
                "mime_type": "text/plain",
                "content": "Hello.",
                "size_bytes": 100,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
        resp = await client.get(
            "/api/v1/memory/projects/p-quota/quota",
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bytes_used"] == 100
    assert body["source_count"] == 1
    assert body["chunk_count"] >= 1


@pytest.mark.asyncio
async def test_quota_exceeded_returns_413(c7_app: FastAPI) -> None:
    # The integration fixture sets the quota to 1 MiB.
    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/projects/p-big/sources",
            json={
                "source_id": "s-big",
                "project_id": "p-big",
                "mime_type": "text/plain",
                "content": "x",
                "size_bytes": 2 * 1024 * 1024,
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Refresh stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_501(c7_app: FastAPI) -> None:
    async with _client(c7_app) as client:
        resp = await client.post(
            "/api/v1/memory/projects/p1/refresh", headers=_HEADERS
        )
    assert resp.status_code == 501
