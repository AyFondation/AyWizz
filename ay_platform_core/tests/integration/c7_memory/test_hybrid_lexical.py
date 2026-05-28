# =============================================================================
# File: test_hybrid_lexical.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_hybrid_lexical.py
# Description: V2 #3-A.b integration tests — the BM25 LEXICAL arm of hybrid
#              retrieval (R-400-202). Real ArangoDB + ArangoSearch view :
#                1. ingest two sources with distinct vocabularies ;
#                2. `lexical_search` for an exact token returns the matching
#                   source (recall the dense arm could miss) and excludes the
#                   unrelated one ;
#                3. BM25 scores are present and ordered.
#              ArangoSearch commits asynchronously, so the search is polled
#              briefly until the view has indexed the freshly-ingested chunks.
#
# @relation validates:R-400-202
# =============================================================================

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import (
    DeterministicHashEmbedder,
)
from ay_platform_core.c7_memory.models import (
    IndexKind,
    RetrievalRequest,
    SourceIngestRequest,
)
from ay_platform_core.c7_memory.service import MemoryService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_SRC_A = (
    "The orchestrator pipeline performs hybrid retrieval. "
    "Requirement R-400-202 fuses BM25 and dense vectors by reciprocal rank."
)
_SRC_B = "Photosynthesis converts sunlight into chemical energy inside plant chloroplasts."


@pytest_asyncio.fixture(scope="function")
async def lexical_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_lex_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = MemoryRepository(db)
    repo._ensure_collections_sync()  # creates the ArangoSearch view too
    embedder = DeterministicHashEmbedder(dimension=64)
    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=embedder.dimension,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
        ),
        repo=repo,
        embedder=embedder,
    )
    try:
        yield {"repo": repo, "service": service, "embedder": embedder}
    finally:
        cleanup_arango_database(arango_container, db_name)


async def _ingest(service: MemoryService, *, source_id: str, content: str) -> None:
    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id="project-lex",
            mime_type="text/plain",
            content=content,
            size_bytes=len(content.encode("utf-8")),
            uploaded_by="alice",
        ),
        tenant_id="tenant-lex",
    )


async def _search_when_indexed(
    repo: MemoryRepository, embedder: DeterministicHashEmbedder, query: str
) -> list[dict[str, Any]]:
    """Poll until ArangoSearch has committed the freshly-ingested chunks."""
    for _ in range(50):  # ~10s budget at 0.2s
        hits = await repo.lexical_search(
            tenant_id="tenant-lex",
            project_id="project-lex",
            query=query,
            indexes=[IndexKind.EXTERNAL_SOURCES.value],
            model_id=embedder.model_id,
            include_deprecated=False,
            include_history=False,
            limit=10,
        )
        if hits:
            return hits
        await asyncio.sleep(0.2)
    return []


async def test_bm25_returns_exact_token_match_and_excludes_unrelated(
    lexical_stack: dict[str, Any],
) -> None:
    service: MemoryService = lexical_stack["service"]
    repo: MemoryRepository = lexical_stack["repo"]
    embedder: DeterministicHashEmbedder = lexical_stack["embedder"]

    await _ingest(service, source_id="src-a", content=_SRC_A)
    await _ingest(service, source_id="src-b", content=_SRC_B)

    hits = await _search_when_indexed(repo, embedder, "hybrid retrieval R-400-202")

    assert hits, "ArangoSearch did not index the chunks within the poll budget"
    names = {h["source_id"] for h in hits}
    assert "src-a" in names  # contains the queried tokens
    assert "src-b" not in names  # unrelated vocabulary — must not match
    assert all("score" in h for h in hits)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)  # BM25 descending


async def test_bm25_matches_the_other_source_for_its_own_token(
    lexical_stack: dict[str, Any],
) -> None:
    service: MemoryService = lexical_stack["service"]
    repo: MemoryRepository = lexical_stack["repo"]
    embedder: DeterministicHashEmbedder = lexical_stack["embedder"]

    await _ingest(service, source_id="src-a", content=_SRC_A)
    await _ingest(service, source_id="src-b", content=_SRC_B)

    hits = await _search_when_indexed(repo, embedder, "photosynthesis chloroplasts")
    names = {h["source_id"] for h in hits}
    assert "src-b" in names
    assert "src-a" not in names


async def test_lexical_search_empty_for_no_match(
    lexical_stack: dict[str, Any],
) -> None:
    service: MemoryService = lexical_stack["service"]
    repo: MemoryRepository = lexical_stack["repo"]
    embedder: DeterministicHashEmbedder = lexical_stack["embedder"]
    await _ingest(service, source_id="src-a", content=_SRC_A)
    # Let the view index, then query a token absent from any source.
    await _search_when_indexed(repo, embedder, "hybrid")
    hits = await repo.lexical_search(
        tenant_id="tenant-lex",
        project_id="project-lex",
        query="zzzznonexistenttoken",
        indexes=[IndexKind.EXTERNAL_SOURCES.value],
        model_id=embedder.model_id,
        include_deprecated=False,
        include_history=False,
        limit=10,
    )
    assert hits == []


async def test_retrieve_hybrid_surfaces_exact_token_match(
    lexical_stack: dict[str, Any],
) -> None:
    """End-to-end through `retrieve()` in the default hybrid mode : the
    BM25 arm surfaces the exact-token source even though the deterministic
    hash embedder gives no meaningful dense signal (R-400-202)."""
    service: MemoryService = lexical_stack["service"]
    repo: MemoryRepository = lexical_stack["repo"]
    embedder: DeterministicHashEmbedder = lexical_stack["embedder"]

    await _ingest(service, source_id="src-a", content=_SRC_A)
    await _ingest(service, source_id="src-b", content=_SRC_B)
    # Wait for the ArangoSearch view to commit so the lexical arm contributes.
    assert await _search_when_indexed(repo, embedder, "hybrid retrieval")

    resp = await service.retrieve(
        RetrievalRequest(
            project_id="project-lex",
            query="hybrid retrieval R-400-202",
            indexes=[IndexKind.EXTERNAL_SOURCES],
            top_k=5,
        ),
        tenant_id="tenant-lex",
    )
    assert "src-a" in {h.source_id for h in resp.hits}
