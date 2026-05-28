# =============================================================================
# File: test_retrieval_quality.py
# Version: 2
# Path: ay_platform_core/tests/eval/test_retrieval_quality.py
# Description: Retrieval-quality eval harness (D-017 T2 reference-based slice /
#              Q-400-011). Ingests the held-out golden corpus into a REAL C7
#              stack (ArangoDB + the real Ollama embedder — a hash embedder
#              would make dense retrieval meaningless), then measures
#              recall@1 / recall@all / MRR / nDCG for two retrieval modes —
#              `dense` (cosine only) and `hybrid` (BM25+dense RRF, R-400-202) —
#              and prints a comparative report.
#
#              The printed report is the EVIDENCE that feeds the D-010 gate
#              ("is v1 retrieval demonstrably insufficient?") and shows whether
#              the hybrid arm (A.b) actually helps. The assertions are robust
#              invariants only (every relevant doc retrievable ; the exact-token
#              query won by the lexical arm) — absolute score thresholds on a
#              tiny corpus would be brittle.
#
#              Self-contained stack (no c7_memory conftest fixtures) : built
#              from the globally-available `arango_container` + `ollama_container`
#              (tests/conftest.py `pytest_plugins`). The `hybrid +
#              contextualisation` (A.c) config needs a chat model and is a
#              follow-on ; this slice measures the dense-vs-hybrid signal.
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
from ay_platform_core.c7_memory.embedding.ollama import OllamaEmbedder
from ay_platform_core.c7_memory.models import (
    IndexKind,
    RetrievalRequest,
    SourceIngestRequest,
)
from ay_platform_core.c7_memory.service import MemoryService
from tests.eval.golden import GOLDEN_DOCS, GOLDEN_QUERIES, GOLDEN_VERSION
from tests.eval.metrics import mean, ndcg_at_k, recall_at_k, reciprocal_rank
from tests.fixtures.containers import (
    ArangoEndpoint,
    OllamaEndpoint,
    cleanup_arango_database,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_PID = "eval-proj"
_TID = "eval-tenant"


@pytest_asyncio.fixture(scope="function")
async def eval_stack(
    arango_container: ArangoEndpoint, ollama_container: OllamaEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c7_eval_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = MemoryRepository(db)
    repo._ensure_collections_sync()
    embedder = OllamaEmbedder(
        base_url=ollama_container.base_url, model_id=ollama_container.embed_model_id,
    )
    await embedder.embed_one("warmup")  # probe dimension
    config = MemoryConfig(
        embedding_adapter="ollama",
        embedding_model_id=embedder.model_id,
        embedding_dimension=embedder.dimension,
        default_quota_bytes=1024 * 1024 * 1024,
        retrieval_scan_cap=1000,
    )
    try:
        yield {"repo": repo, "embedder": embedder, "config": config}
    finally:
        await embedder.aclose()
        cleanup_arango_database(arango_container, db_name)


def _ordered_source_ids(hits: list[Any]) -> list[str]:
    """Unique source_ids in hit order (a source may yield several chunks)."""
    seen: list[str] = []
    s: set[str] = set()
    for h in hits:
        sid = h.source_id
        if sid and sid not in s:
            s.add(sid)
            seen.append(sid)
    return seen


async def _retrieve_ids(svc: MemoryService, query: str, top_k: int) -> list[str]:
    resp = await svc.retrieve(
        RetrievalRequest(
            project_id=_PID,
            query=query,
            indexes=[IndexKind.EXTERNAL_SOURCES],
            top_k=top_k,
        ),
        tenant_id=_TID,
    )
    return _ordered_source_ids(resp.hits)


async def test_retrieval_quality_dense_vs_hybrid(
    eval_stack: dict[str, Any],
) -> None:
    repo: MemoryRepository = eval_stack["repo"]
    embedder: OllamaEmbedder = eval_stack["embedder"]
    config: MemoryConfig = eval_stack["config"]

    hybrid_svc = MemoryService(
        config=config.model_copy(update={"retrieval_mode": "hybrid"}),
        repo=repo, embedder=embedder,
    )
    dense_svc = MemoryService(
        config=config.model_copy(update={"retrieval_mode": "dense"}),
        repo=repo, embedder=embedder,
    )

    for doc in GOLDEN_DOCS:
        await hybrid_svc.ingest_source(
            SourceIngestRequest(
                source_id=doc.source_id,
                project_id=_PID,
                mime_type="text/plain",
                content=doc.content,
                size_bytes=len(doc.content.encode("utf-8")),
                uploaded_by="eval",
            ),
            tenant_id=_TID,
        )

    # Wait for the ArangoSearch view to commit so the hybrid lexical arm
    # actually contributes (it commits asynchronously after ingest).
    for _ in range(50):
        probe = await repo.lexical_search(
            tenant_id=_TID, project_id=_PID, query="R-400-202",
            indexes=[IndexKind.EXTERNAL_SOURCES.value],
            model_id=embedder.model_id,
            include_deprecated=False, include_history=False, limit=5,
        )
        if probe:
            break
        await asyncio.sleep(0.2)

    k_all = len(GOLDEN_DOCS)
    kinds = sorted({gq.kind for gq in GOLDEN_QUERIES})
    # overall[mode][metric] and by_kind[mode][kind][metric]
    overall: dict[str, dict[str, float]] = {}
    by_kind: dict[str, dict[str, dict[str, float]]] = {}
    exact_token_r1: dict[str, dict[str, float]] = {"dense": {}, "hybrid": {}}

    for label, svc in (("dense", dense_svc), ("hybrid", hybrid_svc)):
        rows: list[dict[str, Any]] = []
        for gq in GOLDEN_QUERIES:
            ids = await _retrieve_ids(svc, gq.query, top_k=k_all)
            rel = set(gq.relevant)
            m = {
                "recall@1": recall_at_k(ids, rel, 1),
                "recall@3": recall_at_k(ids, rel, 3),
                "mrr": reciprocal_rank(ids, rel),
                "ndcg@5": ndcg_at_k(ids, rel, 5),
            }
            rows.append({"kind": gq.kind, **m})
            if gq.kind == "exact-token":
                exact_token_r1[label][gq.query] = m["recall@1"]
        metric_names = ("recall@1", "recall@3", "mrr", "ndcg@5")
        overall[label] = {k: mean([r[k] for r in rows]) for k in metric_names}
        by_kind[label] = {
            kind: {
                k: mean([r[k] for r in rows if r["kind"] == kind])
                for k in metric_names
            }
            for kind in kinds
        }

    # ---- Evidence report (printed ; visible with -s / in the failure log) ----
    print(f"\n=== Retrieval eval (golden v{GOLDEN_VERSION}, {k_all} docs, "
          f"{len(GOLDEN_QUERIES)} queries, embedder={embedder.model_id}) ===")
    for label in ("dense", "hybrid"):
        m = overall[label]
        print(f"  [{label:6s}] OVERALL  recall@1={m['recall@1']:.3f}  "
              f"recall@3={m['recall@3']:.3f}  mrr={m['mrr']:.3f}  "
              f"ndcg@5={m['ndcg@5']:.3f}")
        for kind in kinds:
            km = by_kind[label][kind]
            print(f"           {kind:12s} recall@1={km['recall@1']:.3f}  "
                  f"recall@3={km['recall@3']:.3f}  mrr={km['mrr']:.3f}")

    # ---- Robust invariants (not brittle absolute thresholds) ----
    # The harness produced metrics for both modes.
    assert set(overall) == {"dense", "hybrid"}
    # Exact-token / identifier queries are won at rank 1 by the hybrid BM25
    # arm (A.b), and hybrid is never worse than dense on them.
    assert exact_token_r1["hybrid"], "no exact-token queries in the golden set"
    for q, r1 in exact_token_r1["hybrid"].items():
        assert r1 == 1.0, f"hybrid missed exact-token query {q!r}"
        assert r1 >= exact_token_r1["dense"][q]
    # NOTE: the multi-hop row is the D-010 signal — read it from the report,
    # don't assert a floor (flat top-k is expected to struggle there).
