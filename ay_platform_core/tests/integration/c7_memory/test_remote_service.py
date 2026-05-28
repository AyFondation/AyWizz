# =============================================================================
# File: test_remote_service.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_remote_service.py
# Description: Integration test wiring `RemoteMemoryService` against a
#              REAL C7 FastAPI app via httpx ASGITransport. This is as
#              close to a production K8s round-trip as we can get
#              without a kind cluster — the request/response goes
#              through actual FastAPI routing, dependency injection,
#              service orchestration, and Arango storage.
#
#              The integration tier complements the unit tests
#              (`tests/unit/c7_memory/test_remote_service.py`) which
#              pin the wire format with mocked transports. Together
#              they prove: (1) RemoteMemoryService produces the right
#              HTTP shape, AND (2) C7's HTTP surface accepts that
#              shape and returns a parseable response.
#
# @relation validates:R-100-114
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c7_memory.models import (
    IndexKind,
    RetrievalRequest,
    RetrievalResponse,
)
from ay_platform_core.c7_memory.remote import RemoteMemoryService
from ay_platform_core.c7_memory.service import MemoryService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-a",
    "X-User-Roles": "project_editor,project_owner,admin",
}


async def _ingest_source(
    app: FastAPI, *, project_id: str, source_id: str, content: str,
) -> None:
    """Helper — POST an ingestable source through C7's HTTP surface so
    the chunk index is populated before retrieve."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://c7-real",
    ) as client:
        resp = await client.post(
            f"/api/v1/memory/projects/{project_id}/sources",
            json={
                "source_id": source_id,
                "project_id": project_id,
                "mime_type": "text/plain",
                "content": content,
                "size_bytes": len(content.encode("utf-8")),
                "uploaded_by": "alice",
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 201, resp.text


async def test_remote_retrieve_against_live_c7_returns_indexed_content(
    c7_app: FastAPI,
    c7_service: MemoryService,
) -> None:
    """End-to-end: ingest via HTTP, then retrieve via RemoteMemoryService.

    The retrieval response SHALL include the chunk we just ingested,
    proving that:
      - RemoteMemoryService produces a valid POST /api/v1/memory/retrieve
        body that C7's router accepts;
      - the forward-auth headers are honoured by C7's dependency layer;
      - the response JSON parses back into a `RetrievalResponse` Pydantic
        instance without loss.
    """
    await _ingest_source(
        c7_app,
        project_id="p-remote",
        source_id="s-remote",
        content=(
            "The frobulator widget processes thimble streams and emits "
            "structured events to the knowledge graph."
        ),
    )

    transport = httpx.ASGITransport(app=c7_app)
    async with httpx.AsyncClient(transport=transport) as http:
        remote = RemoteMemoryService("http://c7-real", http_client=http)

        response = await remote.retrieve(
            RetrievalRequest(
                project_id="p-remote",
                query="frobulator widget thimble",
                indexes=[IndexKind.EXTERNAL_SOURCES],
                top_k=5,
            ),
            tenant_id="tenant-a",
            user_id="alice",
            user_roles="project_editor",
        )

    assert isinstance(response, RetrievalResponse)
    assert len(response.hits) >= 1, (
        f"expected at least 1 hit, got {response.model_dump()}"
    )
    top = response.hits[0]
    assert top.source_id == "s-remote"
    assert top.score > 0.0
    # The retrieve_id is server-generated (UUID); we don't pin its
    # value, only its presence + non-emptiness.
    assert response.retrieval_id


async def test_remote_retrieve_isolates_by_tenant_via_forward_auth_header(
    c7_app: FastAPI,
) -> None:
    """Tenant isolation per E-100-002 v2: a request with a different
    `X-Tenant-Id` SHALL NOT see another tenant's chunks. RemoteMemory-
    Service propagates the tenant header verbatim, so this test pins
    that the propagation is honoured by C7."""
    await _ingest_source(
        c7_app,
        project_id="p-iso",
        source_id="s-iso",
        content="content visible only to tenant-a",
    )

    # Same project_id, but a different tenant in the forward-auth
    # context — C7 SHALL filter the chunk out.
    transport = httpx.ASGITransport(app=c7_app)
    async with httpx.AsyncClient(transport=transport) as http:
        remote = RemoteMemoryService("http://c7-real", http_client=http)
        response = await remote.retrieve(
            RetrievalRequest(
                project_id="p-iso",
                query="content visible",
                indexes=[IndexKind.EXTERNAL_SOURCES],
                top_k=5,
            ),
            tenant_id="tenant-b",  # DIFFERENT tenant
            user_id="bob",
            user_roles="project_editor",
        )

    assert response.hits == [], (
        f"cross-tenant leak — got {len(response.hits)} hits with tenant-b"
    )
