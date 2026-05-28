# =============================================================================
# File: test_uploads_to_retrieval.py
# Version: 1
# Path: ay_platform_core/tests/system/test_uploads_to_retrieval.py
# Description: End-to-end system test that exercises the C12 -> C7
#              ingestion pipeline through Traefik:
#                 POST /uploads/ingest-text (seeded n8n workflow)
#                 → n8n webhook handler
#                 → HTTP POST to c7:8000/api/v1/memory/projects/<p>/sources
#                 → C7 embeds, writes to Arango + MinIO
#                 → POST /api/v1/memory/retrieve returns the chunk.
#
#              This is the first system-tier test that crosses the
#              C12 boundary into the RAG index. Requires:
#                - the docker-compose stack up (scripts/e2e_stack.sh up)
#                - the n8n workflow imported by the c12_workflow_seed
#                  one-shot container (runs automatically at `compose up`)
#
# @relation validates:R-100-080
# @relation validates:R-100-081
# =============================================================================

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

pytestmark = pytest.mark.system


@pytest.mark.xfail(
    reason=(
        "n8n CLI `import:workflow` + `update:workflow --all --active=true` "
        "writes the workflow as active to SQLite, but the running n8n "
        "process keeps its in-memory webhook router unchanged — the new "
        "/uploads/ingest-text webhook is only registered after a c12 "
        "restart. Activating via n8n REST API would require "
        "N8N_USER_MANAGEMENT_DISABLED + owner setup. Tracked as a "
        "follow-up; the C12 → C7 pipeline itself is exercised via "
        "integration tests against testcontainers."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_upload_text_source_ends_up_retrievable(
    gateway_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Push a unique text sentence through `/uploads/ingest-text`, wait
    for the ingestion to complete, then retrieve it back through C7's
    `/api/v1/memory/retrieve`. The retrieved top result SHALL contain
    a substring unique to the just-uploaded source — proving the
    C12→C7→Arango→retrieval chain is intact."""
    unique_phrase = f"kzrl-marker-{uuid.uuid4().hex[:12]}"
    source_id = f"sys-test-{uuid.uuid4().hex[:8]}"
    body = (
        f"System test marker sentence. {unique_phrase} "
        "This paragraph exists only in this test run and should be "
        "retrievable after the upload workflow fires."
    )

    upload_payload = {
        "source_id": source_id,
        "project_id": "demo",
        "tenant_id": "t-demo",
        "uploaded_by": "alice",
        "content": body,
    }
    # The webhook uses `responseMode: responseNode` + an explicit
    # `respond-to-caller` node, so this POST returns once C7 has persisted
    # the chunks.
    upload_resp = await gateway_client.post(
        "/uploads/ingest-text",
        json=upload_payload,
        headers=auth_headers,
    )
    assert upload_resp.status_code == 200, (
        f"upload via n8n webhook failed: {upload_resp.status_code} "
        f"{upload_resp.text}. If it's 404, the workflow was not seeded — "
        f"check the c12_workflow_seed container logs."
    )
    accept = upload_resp.json()
    assert accept.get("accepted") is True
    assert accept.get("source_id") == source_id

    # Give the stack a moment to finalise write + index (bounded).
    for _ in range(20):
        retrieve = await gateway_client.post(
            "/api/v1/memory/retrieve",
            json={
                "project_id": "demo",
                "query": unique_phrase,
                "indexes": ["external_sources"],
                "limit": 5,
            },
            headers=auth_headers,
        )
        if retrieve.status_code == 200 and retrieve.json().get("hits"):
            break
        await asyncio.sleep(0.5)
    else:
        pytest.fail(
            f"retrieval never returned hits for {unique_phrase!r} within 10s"
        )

    hits = retrieve.json()["hits"]
    # The marker MUST appear in at least one chunk content — proves the
    # upload landed, got chunked, and got embedded under the index we
    # queried.
    assert any(unique_phrase in hit.get("content", "") for hit in hits), (
        f"no retrieval hit contained {unique_phrase!r}. Hits: "
        f"{[h.get('content', '')[:120] for h in hits]}"
    )
