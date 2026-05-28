<!-- =============================================================================
File: 2026-05-23-v2-3-ab-ac-hybrid-contextualisation.md
Version: 1
Path: .claude/sessions/2026-05-23-v2-3-ab-ac-hybrid-contextualisation.md
============================================================================= -->

# Session — 2026-05-23 — V2 #3 Block A : hybrid retrieval + contextualisation

## Context

Completes BLOCK A of V2 #3 (the D-016 v1-compatible subset) on top of A.a
(schema-guided L1 extractor). Two retrieval/ingestion improvements that lift
quality without graph-ML : A.b hybrid retrieval and A.c chunk
contextualisation. Also handled an interleaved diagram/analysis request.

## A.b — hybrid retrieval (R-400-202)

- **`db/repository.py` v3** : an ArangoSearch view `memory_chunks_search`
  (built-in `text_en` analyzer over `content`, low commit/consolidation
  intervals) created in `_ensure_collections_sync` + `lexical_search(...)`
  (AQL `BM25()`, scoped tenant/project/index/model/status).
- **`retrieval/fusion.py`** : `reciprocal_rank_fusion` (pure ; Σ 1/(k+rank)).
- **`service.retrieve`** : fuses the dense (cosine + KG-expansion) ranking with
  the BM25 arm by RRF. `C7_RETRIEVAL_MODE=hybrid` is the DEFAULT (spec),
  `dense`=legacy ; `C7_RRF_K=60`.
- **Stale-view guard** (found via a CI delete-cascade failure) : ArangoSearch
  commits/consolidates async, so just after a hard delete the view can still
  return removed chunks. Fix : when the dense scan is NOT truncated it is the
  complete live set → drop lexical hits absent from it (stale) ; when truncated
  keep them (live chunks beyond scan_cap = the recall win). A real correctness
  fix, not a timing patch.

## A.c — cumulative chunk contextualisation (R-400-203)

- **`contextualizer.py`** : per-chunk document-aware context via C8 (agent
  `c7-contextualizer` → Haiku ; the DOCUMENT is a STABLE system-prompt prefix
  so a prompt-caching provider amortises it). BEST-EFFORT — a per-chunk
  failure yields "" (raw chunk embedded) ; never breaks ingestion.
- **`_index_parsed_source`** : the EMBEDDED text is the contextualised chunk ;
  raw `content` is kept + a new `context` field stored. No-op without an LLM
  (so existing no-LLM ingest tests are unaffected). `C7_CONTEXTUALISATION_*`
  config ; `c7-contextualizer` added to the C8 agent_routes.

## Findings / decisions

- **Index `weights` are a DENSE-arm semantic.** Under the hybrid default RRF
  fuses the unweighted BM25 arm, so a 2× weight no longer deterministically
  owns the top slot — a CI flake on `test_federated_retrieval_merges_indexes`.
  §10.4 D : the test was split — the app path asserts the federated MERGE
  (valid under hybrid) ; a direct DENSE-mode service asserts the weight order
  (where weights are the ranking authority).
- **Interleaved diagram task** : analysed two reference workflow images
  ("8-step agent blueprint", "RAG vs Agentic RAG") vs AyWizz. Verdict : strong
  7/8 coverage ; the recurring GAP is **Evals (D-017)** — the same missing
  instrument that gates B (D-010) and the Q13 POC. Our chat-RAG is still the
  linear RAG ; the agentic-retrieval loop is D-016 v2 (B, gated). Redrew
  `requirements/050-WORKFLOW-prompt-execution.svg` (v2) as a 2-band blueprint
  (build-time cards + run-time agentic flow), replacing the v1 sequence diagram.

## Verification

`run_tests.sh ci` — ruff OK → mypy OK → pytest **1614 passed, 1 skipped**,
coverage **87.32%**. CI surfaced + fixed mid-session : the delete-cascade
stale-view bug, an env `chunk_token_size>=16` floor, the weight/RRF flake.

## Next

Operator chose the **eval harness (D-017)** as the next step — highest
leverage : it unblocks the D-010 gate for B, measures whether A.b/A.c help,
and is the Q13 POC criterion. Then B (on evidence) ; C (bi-temporal) needs a
§8.1 D-016 amendment first.
