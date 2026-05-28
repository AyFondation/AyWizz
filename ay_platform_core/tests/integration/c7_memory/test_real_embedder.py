# =============================================================================
# File: test_real_embedder.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_real_embedder.py
# Description: Integration tests against a REAL embedding model — Ollama's
#              `all-minilm` (384-dim). Complements `test_embedder.py`
#              which validates the deterministic-hash baseline; here we
#              prove `OllamaEmbedder` speaks the real API and that C7's
#              retrieval produces meaningful cosine ranks when backed by
#              actual semantic vectors.
#
#              Marked `slow` (model pull + inference) so CI can skip by
#              marker expression when needed.
# =============================================================================

from __future__ import annotations

import hashlib

import pytest

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.ollama import OllamaEmbedder
from ay_platform_core.c7_memory.models import (
    IndexKind,
    RetrievalRequest,
    SourceIngestRequest,
)
from ay_platform_core.c7_memory.service import MemoryService
from tests.fixtures.containers import OllamaEndpoint

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@pytest.mark.asyncio
async def test_ollama_embedder_returns_consistent_vectors(
    ollama_container: OllamaEndpoint,
) -> None:
    """Same text → same vector, across two separate calls. Proves the
    adapter passes the prompt verbatim and Ollama's model is
    deterministic for inference (temperature=0 semantics at the embedder
    layer)."""
    embedder = OllamaEmbedder(
        base_url=ollama_container.base_url,
        model_id=ollama_container.embed_model_id,
    )
    try:
        text = "A platform SHALL retrieve relevant documents from its memory."
        v1 = await embedder.embed_one(text)
        v2 = await embedder.embed_one(text)
    finally:
        await embedder.aclose()

    assert len(v1) == len(v2)
    assert len(v1) > 0, "Ollama returned an empty embedding vector"
    # Dimension SHALL be the one Ollama advertises; `all-minilm` is 384.
    assert embedder.dimension == len(v1)
    # Sanity: bit-exact equality between two calls on the same text.
    assert v1 == v2, "Ollama embedder produced non-deterministic vectors"


@pytest.mark.asyncio
async def test_ollama_embedder_distinguishes_topics(
    ollama_container: OllamaEndpoint,
) -> None:
    """Semantically different texts SHALL produce vectors whose cosine
    similarity is measurably lower than the self-similarity (1.0). This
    is the minimal signal that the embedder is doing something
    semantically meaningful — unlike the deterministic-hash embedder
    whose similarity is bag-of-words-like."""
    from ay_platform_core.c7_memory.retrieval.similarity import cosine  # noqa: PLC0415

    embedder = OllamaEmbedder(
        base_url=ollama_container.base_url,
        model_id=ollama_container.embed_model_id,
    )
    try:
        v_cat = await embedder.embed_one("A domestic cat sleeps on a warm pillow.")
        v_cat_twin = await embedder.embed_one(
            "A housecat is napping on a soft cushion."
        )
        v_rocket = await embedder.embed_one(
            "The SpaceX Falcon 9 completed its boostback burn."
        )
    finally:
        await embedder.aclose()

    sim_close = cosine(v_cat, v_cat_twin)
    sim_far = cosine(v_cat, v_rocket)
    assert sim_close > sim_far, (
        f"Expected cat/housecat closer than cat/rocket — got close={sim_close:.3f}, "
        f"far={sim_far:.3f}. The embedder may be misconfigured."
    )


@pytest.mark.asyncio
async def test_ingest_and_retrieve_with_real_embedder(
    ollama_container: OllamaEndpoint,
    c7_repo: MemoryRepository,
) -> None:
    """End-to-end: ingest two sources with semantically different content,
    then retrieve with a query semantically close to ONE of them. The
    closer source's chunk SHALL rank first. With the deterministic-hash
    embedder this would be bag-of-words; with Ollama it's actual
    semantic retrieval."""
    embedder = OllamaEmbedder(
        base_url=ollama_container.base_url,
        model_id=ollama_container.embed_model_id,
    )
    # Force the embedder to probe its dimension before passing to C7 so
    # MemoryConfig.embedding_dimension can be set accurately.
    await embedder.embed_one("warmup")
    config = MemoryConfig(
        embedding_adapter="ollama",
        embedding_model_id=ollama_container.embed_model_id,
        embedding_dimension=embedder.dimension,
        chunk_token_size=64,
        chunk_overlap=8,
        retrieval_scan_cap=1000,
        default_quota_bytes=1024 * 1024,
    )
    service = MemoryService(config=config, repo=c7_repo, embedder=embedder)

    tenant = "t-real-embed"
    project = "demo"

    try:
        for src_id, content in (
            (
                "src-cat",
                "House cats are domestic felines kept as pets. They purr, "
                "nap on soft surfaces, and groom themselves frequently.",
            ),
            (
                "src-rocket",
                "Falcon 9 is a partially reusable two-stage-to-orbit "
                "launch vehicle built by SpaceX for Earth-orbit missions.",
            ),
        ):
            # Size check needs an int; use len(content) as byte proxy
            # (ASCII content).
            await service.ingest_source(
                SourceIngestRequest(
                    source_id=src_id,
                    project_id=project,
                    mime_type="text/plain",
                    content=content,
                    size_bytes=len(content),
                    uploaded_by="alice",
                ),
                tenant_id=tenant,
            )

        # Query semantically close to the cat source.
        result = await service.retrieve(
            RetrievalRequest(
                project_id=project,
                query="Where do pet cats usually sleep?",
                indexes=[IndexKind.EXTERNAL_SOURCES],
                top_k=3,
            ),
            tenant_id=tenant,
        )
        hits = result.hits
        assert len(hits) > 0
        # The top hit SHALL belong to the cat source, not the rocket one.
        assert hits[0].source_id == "src-cat", (
            f"Expected top hit from src-cat, got {hits[0].source_id!r} "
            f"(all hits: {[h.source_id for h in hits]})"
        )
    finally:
        await embedder.aclose()

    # Sanity-check that content_hash pipeline still agrees with the body
    # even with the real embedder. Any drift here would indicate the
    # adapter swap broke the ingestion contract.
    chunks = await c7_repo.scan_chunks(
        tenant_id=tenant,
        project_id=project,
        indexes=["external_sources"],
        model_id=ollama_container.embed_model_id,
        include_deprecated=False,
        include_history=False,
        scan_cap=100,
    )
    for row in chunks:
        expected = "sha256:" + hashlib.sha256(
            row["content"].encode("utf-8")
        ).hexdigest()
        assert row["content_hash"] == expected
