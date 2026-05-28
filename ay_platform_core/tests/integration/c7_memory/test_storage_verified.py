# =============================================================================
# File: test_storage_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_storage_verified.py
# Description: Storage-verified integration tests for C7. Ingests a source
#              via the service, then reads raw ArangoDB state to check:
#                - `memory_sources` row present with correct tenant,
#                  project, model_id, chunk_count.
#                - `memory_chunks` rows match the reported chunk_count,
#                  each with a correctly-dimensioned vector and a
#                  content_hash that is SHA-256 of the chunk body.
# =============================================================================

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c7_memory.models import SourceIngestRequest
from ay_platform_core.c7_memory.service import MemoryService
from tests.fixtures.containers import ArangoEndpoint

pytestmark = pytest.mark.integration


def _raw_arango(endpoint: ArangoEndpoint, db_name: str) -> Any:
    return ArangoClient(hosts=endpoint.url).db(
        db_name, username=endpoint.username, password=endpoint.password
    )


_BODY = (
    "The platform SHALL ingest text sources into the memory service. "
    "Each source is chunked with token-level overlap and embedded into "
    "ArangoDB as retrievable vectors tagged by model id."
)


@pytest.mark.asyncio
async def test_ingested_source_rows_land_in_arango(
    c7_service: MemoryService,
    c7_repo: Any,
    arango_container: ArangoEndpoint,
) -> None:
    payload = SourceIngestRequest(
        source_id="verif-src-001",
        project_id="demo",
        mime_type="text/plain",
        content=_BODY,
        size_bytes=len(_BODY),
        uploaded_by="alice",
    )
    source = await c7_service.ingest_source(payload, tenant_id="t-demo")

    db = _raw_arango(arango_container, c7_repo._db.name)

    # --- memory_sources row: present + fields consistent with service response ---
    cursor = db.aql.execute(
        """
        FOR s IN memory_sources
            FILTER s.tenant_id == @t AND s.project_id == @p AND s.source_id == @sid
            RETURN s
        """,
        bind_vars={"t": "t-demo", "p": "demo", "sid": payload.source_id},
    )
    rows = list(cursor)
    assert len(rows) == 1, (
        f"expected exactly 1 memory_sources row for source {payload.source_id}, "
        f"found {len(rows)}"
    )
    row = rows[0]
    assert row["chunk_count"] == source.chunk_count
    assert row["uploaded_by"] == "alice"
    assert row["mime_type"] == "text/plain"
    # The model_id recorded in the source row SHALL match the active
    # embedder reported by the service. With Ollama it's the pulled
    # model id (e.g. "all-minilm"); with the deterministic baseline it
    # starts with "deterministic-hash".
    assert row["model_id"] == c7_service._embedder.model_id

    # --- memory_chunks rows: count matches + vector dimension + content hash ---
    cursor = db.aql.execute(
        """
        FOR c IN memory_chunks
            FILTER c.tenant_id == @t AND c.project_id == @p AND c.source_id == @sid
            SORT c.chunk_index ASC
            RETURN c
        """,
        bind_vars={"t": "t-demo", "p": "demo", "sid": payload.source_id},
    )
    chunks = list(cursor)
    assert len(chunks) == source.chunk_count, (
        f"memory_chunks count {len(chunks)} disagrees with source.chunk_count "
        f"{source.chunk_count}"
    )
    expected_dim = c7_service._embedder.dimension
    for c in chunks:
        assert len(c["vector"]) == expected_dim, (
            f"vector dimension drift: {len(c['vector'])} != {expected_dim}"
        )
        expected_hash = "sha256:" + hashlib.sha256(c["content"].encode("utf-8")).hexdigest()
        assert c["content_hash"] == expected_hash, (
            "memory_chunks.content_hash does not match SHA-256 of content body "
            "— hash/body drift on ingest"
        )
        assert c["status"] == "active"
        assert c["index"] == "external_sources"


@pytest.mark.asyncio
async def test_delete_source_removes_all_chunks(
    c7_service: MemoryService,
    c7_repo: Any,
    arango_container: ArangoEndpoint,
) -> None:
    payload = SourceIngestRequest(
        source_id="verif-del-002",
        project_id="demo",
        mime_type="text/plain",
        content=_BODY,
        size_bytes=len(_BODY),
        uploaded_by="alice",
    )
    await c7_service.ingest_source(payload, tenant_id="t-demo")
    await c7_service.delete_source("t-demo", "demo", payload.source_id)

    db = _raw_arango(arango_container, c7_repo._db.name)

    # memory_sources row SHALL be gone.
    cursor = db.aql.execute(
        """
        FOR s IN memory_sources
            FILTER s.tenant_id == @t AND s.project_id == @p AND s.source_id == @sid
            RETURN 1
        """,
        bind_vars={"t": "t-demo", "p": "demo", "sid": payload.source_id},
    )
    assert list(cursor) == [], "memory_sources row survived delete_source"

    # memory_chunks rows SHALL be gone too — leftover vectors would poison
    # retrieval with references to a non-existent source.
    cursor = db.aql.execute(
        """
        FOR c IN memory_chunks
            FILTER c.source_id == @sid
            RETURN 1
        """,
        bind_vars={"sid": payload.source_id},
    )
    remaining = list(cursor)
    assert remaining == [], (
        f"memory_chunks rows survived delete_source: {len(remaining)} orphan chunks"
    )
