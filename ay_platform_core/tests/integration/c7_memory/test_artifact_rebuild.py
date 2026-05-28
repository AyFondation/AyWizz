# =============================================================================
# File: test_artifact_rebuild.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_artifact_rebuild.py
# Description: Integration test for R-400-207 — the vector store rebuilds
#              from the MinIO chunks.json artifact WITHOUT re-embedding.
#              Proof: rebuild into a FRESH ArangoDB using a service whose
#              embedder RAISES if called; the chunks are restored and the
#              poison embedder is never touched.
#
# @relation validates:R-400-207
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c7_memory.artifacts import CHUNKS_ARTIFACT
from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.models import IndexKind
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database
from tests.integration.c7_memory.conftest import _ingest_text_via_chunks

_TENANT = "t-rebuild"
_PROJECT = "p-rebuild"
_SOURCE = "src-rebuild-1"
_TEXT = (
    "Marie Curie discovered polonium and radium. She taught at the Sorbonne "
    "in Paris. Her work on radioactivity earned two Nobel prizes across "
    "physics and chemistry over the following years of research."
)


class _PoisonEmbedder:
    """EmbeddingProvider whose embed methods MUST never be called during a
    replay-based rebuild (R-400-207). model_id/dimension match the ingest
    embedder so the restored rows line up; the methods raise on use."""

    model_id = "deterministic-hash-v1"
    dimension = 64
    max_input_tokens = 8192

    def __init__(self) -> None:
        self.calls = 0

    async def embed_one(self, text: str) -> list[float]:
        self.calls += 1
        raise AssertionError("rebuild must not re-embed (R-400-207)")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        raise AssertionError("rebuild must not re-embed (R-400-207)")


@pytest.fixture(scope="function")
def fresh_repo(arango_container: ArangoEndpoint) -> Iterator[MemoryRepository]:
    """A second, empty ArangoDB standing in for a wiped/lost vector store."""
    db_name = f"c7_rebuild_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = MemoryRepository(db)
        repo._ensure_collections_sync()
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


async def test_rebuild_restores_vector_store_without_re_embedding(
    c7_upload_service: MemoryService,
    c7_storage: MemorySourceStorage,
    fresh_repo: MemoryRepository,
) -> None:
    # 1. Ingest a source — chunks land in Arango AND chunks.json in MinIO.
    # D-020 session 7 — uses the new `/ingest-chunks` path via the shared
    # `_ingest_text_via_chunks` conftest helper (was: ingest_uploaded_source).
    public = await _ingest_text_via_chunks(
        service=c7_upload_service,
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id=_SOURCE,
        text=_TEXT,
    )
    assert public.chunk_count >= 1

    # 2. The replay artifact exists in MinIO (R-400-207).
    raw = await c7_storage.get_artifact(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id=_SOURCE,
        name=CHUNKS_ARTIFACT,
    )
    assert raw, "chunks.json artifact was not persisted on ingest"

    # 3. Simulate DB loss: rebuild into a FRESH Arango with a service whose
    #    embedder raises if called. The same MinIO holds the artifacts.
    poison = _PoisonEmbedder()
    rebuild_service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_model_id=poison.model_id,
            embedding_dimension=poison.dimension,
            chunk_token_size=64,
            chunk_overlap=8,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=fresh_repo,
        embedder=poison,
        storage=c7_storage,
    )

    result = await rebuild_service.rebuild_from_artifacts(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id=_SOURCE,
    )

    # 4. The vector store is restored, and the embedder was NEVER called.
    assert result["chunks"] == public.chunk_count
    assert poison.calls == 0, "rebuild re-embedded — violates R-400-207"

    restored = await fresh_repo.scan_chunks(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        indexes=[IndexKind.EXTERNAL_SOURCES.value],
        model_id=poison.model_id,
        include_deprecated=False,
        include_history=False,
        scan_cap=1000,
    )
    restored_for_source = [c for c in restored if c.get("source_id") == _SOURCE]
    assert len(restored_for_source) == public.chunk_count
    # Vectors came back verbatim from the artifact, not recomputed.
    assert all(c.get("vector") for c in restored_for_source)
