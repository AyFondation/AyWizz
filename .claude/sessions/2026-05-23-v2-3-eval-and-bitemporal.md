<!-- =============================================================================
File: 2026-05-23-v2-3-eval-and-bitemporal.md
Version: 1
Path: .claude/sessions/2026-05-23-v2-3-eval-and-bitemporal.md
============================================================================= -->

# Session — 2026-05-23 — V2 #3 : retrieval eval harness + C bi-temporal

## Context

After Block A (D-016 v1 subset : A.a/A.b/A.c), the operator chose to do three
things in order : (1) the eval harness, (2) C bi-temporal, (3) consolidate.
The eval was prioritised because measurement is the missing instrument that
gates several decisions (D-010 for B, the Q13 POC, "does A.b/A.c help").

## Eval harness — D-017 retrieval slice (Q-400-011)

Scoped to the **retrieval-quality slice** (T2 reference-based), NOT the full
D-017 (graded Verdicts + C6 plugin + T3 judge across 600/700/800, which is
spec-queued — §8.1, don't code against unwritten spec).

- `tests/eval/metrics.py` — pure `recall@k` / `precision@k` / `nDCG@k` / `MRR`
  (12 unit tests).
- `tests/eval/golden.py` — held-out, versioned golden set (`GOLDEN_VERSION=2`,
  10 docs / 8 queries) mixing semantic, near-duplicate, exact-token/identifier,
  and a **multi-hop** query (the flat-RAG failure mode).
- `tests/eval/test_retrieval_quality.py` — runner over a real C7 stack (real
  Ollama embedder — a hash embedder makes dense meaningless ; built inline from
  the global `arango_container`/`ollama_container`). Measures dense vs hybrid,
  prints a by-kind comparative report ; asserts robust invariants only
  (exact-token won by the hybrid BM25 arm) — absolute thresholds on a tiny
  corpus would be brittle.

**Measured finding (the point of the exercise).** On the golden set, BOTH dense
and hybrid reach recall@3 = 1.0 across every query kind — even multi-hop
(both hop docs land in the top-3) and identifiers (all-minilm resolves them).
→ **No demonstrated v1 insufficiency → the D-010 gate STAYS CLOSED → B
(GraphRAG L2/L3 + iterative retrieval) is NOT justified by evidence.** Truly
stressing flat-RAG into failure needs a production-scale corpus, not a CI
fixture. Honest, measured verdict instead of building B on intuition.

## C — bi-temporal KG (D-019)

§8.1 spec gap (bi-temporal absent from D-016) → spec FIRST, validated by the
operator (D-019 KG-first), then code :

- **Spec** : `999-SYNTHESIS` D-019 (bi-temporal, ArangoDB-native, append-only)
  + `400-SPEC` R-400-209 (the requirement, with as-of semantics).
- **Model** : nullable `valid_from`/`valid_to` on StructuralEntity/Relation.
- **Repo** (`kg/repository.py` v5) : `persist_structural` stamps valid-time +
  transaction-time (`recorded_at`/`superseded_at`) columns ; `supersede_relation`
  is the **append-only** correction (close every open version, insert a new one
  — nothing deleted) ; `relations_as_of(valid_at=…, known_as_of=…)` answers
  "true at world-time t" / "known at system-time s" (ISO-string interval
  filters ; null = open/timeless).
- Realised **ArangoDB-natively** — NOT the `graphiti` package (Neo4j/FalkorDB),
  which would break D-002's unified store. This is the "Graphiti bi-temporal"
  hallmark without the second store.

## Verification

`run_tests.sh ci` — ruff OK → mypy OK → pytest **1630 passed, 1 skipped**,
coverage **87.35%**. Spec coherence green (D-019 / R-400-209 declared ; the new
`@relation implements:R-400-209` markers point to declared entities).

## Next (all gated / queued)

B only on production-scale eval evidence ; golden-set expansion to probe D-010 ;
full D-017 (graded Verdicts + C6 plugin + judge) is spec-first ; C follow-ons
(open-domain extractor + chunk valid-time). Clean pause point.
