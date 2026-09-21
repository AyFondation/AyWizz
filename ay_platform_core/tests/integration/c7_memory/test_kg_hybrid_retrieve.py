# =============================================================================
# File: test_kg_hybrid_retrieve.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_kg_hybrid_retrieve.py
# Description: Phase F.2 integration test — KG-based hybrid retrieval.
#              Wires real ArangoDB + a KGRepository populated by hand
#              (no LLM round-trip needed; F.1's extractor is exercised
#              elsewhere). Verifies that:
#                1. With the graph empty, retrieve behaves like pure
#                   vector — graph-related-but-lower-cosine chunks do
#                   NOT surface in top_k when a stronger non-graph
#                   chunk exists.
#                2. With the graph populated such that a low-cosine
#                   chunk is graph-neighbour of the top seed, that
#                   chunk's score gets boosted (config boost = 2.0)
#                   enough to overtake a higher-cosine non-graph chunk
#                   and surface in top_k.
#
# @relation validates:R-400-040
# =============================================================================

from __future__ import annotations

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
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.models import (
    IndexKind,
    KGEntity,
    KGRelation,
    RetrievalRequest,
    SourceIngestRequest,
)
from ay_platform_core.c7_memory.service import MemoryService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


@pytest_asyncio.fixture(scope="function")
async def hybrid_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_hybrid_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )

    repo = MemoryRepository(db)
    repo._ensure_collections_sync()
    kg_repo = KGRepository(db)
    kg_repo._ensure_collections_sync()
    # dim=1024 keeps hash-bucket collisions << 1% on the small test
    # vocabulary, so the cosine-similarity arithmetic in the test
    # comments holds in practice.
    embedder = DeterministicHashEmbedder(dimension=1024)

    # Boost = 2.0 makes the F.2 effect dramatic enough to fit a single
    # observable assertion. Production default is 1.3 (subtler). The
    # test verifies the MECHANISM, not the production tuning.
    service = MemoryService(
        config=MemoryConfig(
            chunk_token_size=64,
            chunk_overlap=8,
            default_quota_bytes=1024 * 1024 * 1024,
            retrieval_scan_cap=1000,
            kg_expansion_boost=2.0,
            kg_expansion_depth=1,
            kg_expansion_neighbour_cap=10,
        ),
        repo=repo,
        embedder=embedder,
        kg_repo=kg_repo,
    )
    try:
        yield {"service": service, "kg_repo": kg_repo, "repo": repo}
    finally:
        cleanup_arango_database(arango_container, db_name)


_TENANT = "tenant-hybrid"
_PROJECT = "project-hybrid"


async def _ingest(service: MemoryService, source_id: str, text: str) -> None:
    await service.ingest_source(
        SourceIngestRequest(
            source_id=source_id,
            project_id=_PROJECT,
            mime_type="text/plain",
            content=text,
            size_bytes=len(text.encode("utf-8")),
            uploaded_by="alice",
        ),
        tenant_id=_TENANT,
    )


async def test_retrieve_pure_vector_when_graph_is_empty(
    hybrid_stack: dict[str, Any],
) -> None:
    """Baseline. With kg_repo wired but the graph empty, retrieve
    SHALL produce the same ranking as pure-vector cosine — no boost,
    no expansion. Concretely: a graph-related-but-lower-cosine chunk
    does NOT surface in top_k when a stronger non-graph chunk exists."""
    service: MemoryService = hybrid_stack["service"]

    # Cosine to query "rocket fuel" (bag-of-hashed-tokens, dim=1024):
    #   alpha "rocket fuel"               → 2/sqrt(2*2) = 1.000
    #   beta  "rocket apple banana"       → 1/sqrt(2*3) ≈ 0.408
    #   gamma "rocket cherry"             → 1/sqrt(2*2) = 0.500
    await _ingest(service, "src-alpha", "rocket fuel")
    await _ingest(service, "src-beta", "rocket apple banana")
    await _ingest(service, "src-gamma", "rocket cherry")

    response = await service.retrieve(
        RetrievalRequest(
            project_id=_PROJECT,
            query="rocket fuel",
            indexes=[IndexKind.EXTERNAL_SOURCES],
            top_k=2,
        ),
        tenant_id=_TENANT,
    )
    source_ids = [hit.source_id for hit in response.hits]
    # Pure-vector: alpha (1.0) > gamma (0.5) > beta (0.41). Top-2 is
    # alpha + gamma; beta is NOT in the result.
    assert source_ids == ["src-alpha", "src-gamma"]


async def test_retrieve_graph_boost_surfaces_neighbour_chunk(
    hybrid_stack: dict[str, Any],
) -> None:
    """Hybrid expansion. After populating the graph with an edge from
    an entity in alpha to an entity in beta, retrieve SHALL boost
    beta's score (cosine 0.408 x 2.0 = 0.816) past gamma's
    (cosine 0.5), so top_k=2 returns {alpha, beta}. gamma drops out."""
    service: MemoryService = hybrid_stack["service"]
    kg_repo: KGRepository = hybrid_stack["kg_repo"]

    await _ingest(service, "src-alpha", "rocket fuel")
    await _ingest(service, "src-beta", "rocket apple banana")
    await _ingest(service, "src-gamma", "rocket cherry")

    # Populate the graph by hand — F.2 is independent of F.1's LLM
    # extractor; the data shape is what matters for the traversal.
    # Alpha mentions "rocket" entity. Beta mentions "apple" entity.
    # Edge rocket → apple links them. find_neighbor_source_ids(seed=alpha)
    # walks rocket → apple and returns beta.
    rocket = KGEntity(name="rocket", type="thing")
    apple = KGEntity(name="apple", type="thing")
    await kg_repo.persist(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id="src-alpha",
        entities=[rocket],
        relations=[],
    )
    await kg_repo.persist(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        source_id="src-beta",
        entities=[apple],
        relations=[
            KGRelation(subject=rocket, relation="related_to", object=apple),
        ],
    )

    response = await service.retrieve(
        RetrievalRequest(
            project_id=_PROJECT,
            query="rocket fuel",
            indexes=[IndexKind.EXTERNAL_SOURCES],
            top_k=2,
        ),
        tenant_id=_TENANT,
    )
    source_ids = [hit.source_id for hit in response.hits]
    # Hybrid: alpha (seed, 1.0) > beta (boosted 0.41 x 2.0 = 0.82) >
    # gamma (unboosted 0.5). Top-2 is alpha + beta; gamma drops out.
    assert source_ids == ["src-alpha", "src-beta"]


async def test_retrieve_pulls_in_chunks_beyond_scan_cap(
    arango_container: ArangoEndpoint,
) -> None:
    """Proposition A — pool widening. With `retrieval_scan_cap` set to
    2 (smaller than the corpus), pure-vector retrieve only sees the
    first 2 chunks Arango returns. KG expansion SHALL fetch chunks of
    graph-neighbour source_ids that the scan missed, scoring them
    alongside the seeds before the top_k cut."""
    db_name = f"c7_capwiden_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    try:
        db = ArangoClient(hosts=arango_container.url).db(
            db_name, username="root", password=arango_container.password,
        )
        repo = MemoryRepository(db)
        repo._ensure_collections_sync()
        kg_repo = KGRepository(db)
        kg_repo._ensure_collections_sync()
        embedder = DeterministicHashEmbedder(dimension=1024)
        service = MemoryService(
            config=MemoryConfig(
                chunk_token_size=64,
                chunk_overlap=8,
                default_quota_bytes=1024 * 1024 * 1024,
                # Scan cap deliberately tiny to force F.2's pool
                # widening to be the only way the third source can
                # appear in top_k.
                retrieval_scan_cap=2,
                # Boost neutralised so this test isolates proposition A
                # (the FETCH path) from proposition B (the boost).
                kg_expansion_boost=1.0,
                kg_expansion_depth=1,
                kg_expansion_neighbour_cap=10,
            ),
            repo=repo,
            embedder=embedder,
            kg_repo=kg_repo,
        )

        # 3 sources, scan_cap=2 → the scan can only ever see two of them.
        #
        # WHICH two is NOT ours to choose, and this test used to assume it
        # was. Its predecessor said "keys ordered so the AQL primary-index
        # scan (insertion order) returns alpha + beta first" and asserted
        # `"src-3-gamma" not in baseline_ids`. But `scan_chunks` issues
        # `FOR c IN memory_chunks FILTER ... LIMIT @scan_cap RETURN c` with
        # NO `SORT` (db/repository.py), and ArangoDB promises no ordering for
        # an unsorted collection scan. The assumption held most of the time
        # and failed intermittently in a full-suite run on 2026-09-20.
        #
        # It was also VACUOUS in the other direction: had the scan cut `beta`
        # instead, gamma would already be in the baseline and the closing
        # `assert "src-3-gamma" in with_kg_ids` would have passed without the
        # expansion doing anything at all.
        #
        # So the test now asserts the REQUIREMENT rather than one arrangement
        # of it: whatever the scan cut off, KG expansion brings it back.
        await _ingest(service, "src-1-alpha", "rocket fuel oxygen hydrogen")
        await _ingest(service, "src-2-beta", "garden flower meadow")
        await _ingest(service, "src-3-gamma", "rocket fuel propellant")
        all_sources = {"src-1-alpha", "src-2-beta", "src-3-gamma"}

        # Without KG: the scan cap cuts at least one source out of the pool
        # entirely, so it cannot reach top_k however well it scores.
        baseline = await service.retrieve(
            RetrievalRequest(
                project_id=_PROJECT,
                query="rocket fuel",
                indexes=[IndexKind.EXTERNAL_SOURCES],
                top_k=3,
            ),
            tenant_id=_TENANT,
        )
        baseline_ids = {hit.source_id for hit in baseline.hits}
        # The precondition itself, asserted rather than assumed: top_k=3 would
        # admit all three, so anything missing was cut by the scan, not by
        # ranking.
        cut_by_scan = all_sources - baseline_ids
        assert cut_by_scan, (
            f"scan_cap=2 over 3 sources must exclude at least one, got "
            f"{baseline_ids}"
        )

        # Populate the KG so the cut source is one hop from a surviving one,
        # WHICHEVER pair the scan kept. One entity per source, joined in a
        # triangle.
        #
        # THE ENTITY-PER-SOURCE PART IS LOAD-BEARING, and not obviously so.
        # `find_neighbor_source_ids` builds `seeds` from every entity that
        # mentions a scanned source, then traverses with
        # `FILTER v._key NOT IN seed_keys`. An entity mentioned by BOTH a
        # survivor and the cut source is therefore a seed, and is excluded
        # from its own neighbour set — so the cut source becomes unreachable
        # through it. Sharing one concept between two sources silently
        # disables the very expansion under test (observed while writing
        # this, 2026-09-20).
        concepts = {
            "src-1-alpha": KGEntity(name="rocket-fuel-system", type="concept"),
            "src-2-beta": KGEntity(name="garden-bed", type="concept"),
            "src-3-gamma": KGEntity(name="propellant", type="concept"),
        }
        for source_id, own in concepts.items():
            await kg_repo.persist(
                tenant_id=_TENANT, project_id=_PROJECT, source_id=source_id,
                entities=[own], relations=[],
            )

        # The edges are persisted under a source that owns NO CHUNKS, and
        # that is the second load-bearing subtlety. `_upsert_entity` appends
        # the persisting `source_id` to EVERY entity it touches — including
        # the endpoints named in `relations`, which it resolves leniently.
        # Declaring the triangle from any of the three real sources would
        # therefore make all three concepts mention all three sources, making
        # every concept a seed, and `FILTER v._key NOT IN seed_keys` would
        # then filter the entire neighbour set away. A link-only source keeps
        # each concept mentioned by exactly one SCANNABLE source.
        link_relations = [
            KGRelation(subject=a, relation="related_to", object=b)
            for i, a in enumerate(concepts.values())
            for b in list(concepts.values())[i + 1 :]
        ]
        await kg_repo.persist(
            tenant_id=_TENANT, project_id=_PROJECT, source_id="src-0-links",
            entities=[], relations=link_relations,
        )

        # With KG: a scanned source seeds the traversal, its neighbours name
        # the cut source, whose chunks are fetched OUT-OF-SCAN and scored.
        # Boost is 1.0, so pool widening is the only mechanism at work — a
        # recovered source proves the FETCH path, not a ranking nudge.
        with_kg = await service.retrieve(
            RetrievalRequest(
                project_id=_PROJECT,
                query="rocket fuel",
                indexes=[IndexKind.EXTERNAL_SOURCES],
                top_k=3,
            ),
            tenant_id=_TENANT,
        )
        with_kg_ids = {hit.source_id for hit in with_kg.hits}
        assert cut_by_scan <= with_kg_ids, (
            f"KG expansion must recover what the scan cap cut: "
            f"{cut_by_scan} missing from {with_kg_ids}"
        )
    finally:
        cleanup_arango_database(arango_container, db_name)
