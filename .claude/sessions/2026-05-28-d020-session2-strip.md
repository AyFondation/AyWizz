<!-- =============================================================================
File: 2026-05-28-d020-session2-strip.md
Version: 1
Path: .claude/sessions/2026-05-28-d020-session2-strip.md
Description: D-020 session 2/7 — spec amendments v2 (5 optimisations) +
             physical strip of dead modules in ay_extractor/src/.
============================================================================= -->

# Session — D-020 v2 spec amendments + AyExtractor strip (2026-05-28)

## Context

Operator's directive in the wake of D-020 v1: *"est il possible d'optimiser
/ rendre plus efficient le processus / workflow déjà défini ? c'est à dire
améliorer la qualité tout en maîtrisant et/ou diminuant les coûts"*.

Critical-partner review of D-020 v1 surfaced 7 leverage points; 5 were
retained (3 mandatory win-wins, 1 architectural shift, 1 opt-in), 2 deferred
(C2 chunk-dedup across documents → Q-200-026 future; C3 adaptive Phase 1 →
Q-200-028). Spec amended **before** the strip so sessions 3-7 implement
against the v2 design, not v1.

## Part 1 — Spec amendments v2 (D-020 v1 → v2)

5 optimisations validated by AskUserQuestion and landed in spec:

### A1' — 2-tier LLM gating for the decontextualiser
**Reformulated** during the operator review: operator pushed back on the
original regex-based heuristic ("ne marche pas sur 'l'entreprise' / 'le
système'"). Replaced by a cheap LLM **screener** (R-800-134, Haiku-class)
deciding per-chunk if the expensive Sonnet decontextualiser is needed.
Captures semantic ambiguities a regex would miss while saving ~30-50% of
expensive calls. Break-even at ≥ 2% skip rate. R-800-131 v2 conditions
the decontextualiser invocation on screener YES; v1's Haiku-only routing
becomes Sonnet-via-screener-gate.

### A2 — Mandatory `cache_control` prompt-marker structure
R-800-131 v2 + R-800-132 v2 make the prompt-caching cache marker placement
normative (was: `prompt_caching` feature required but marker placement
implicit). Without the explicit marker, providers don't key the cache and
the 10× discount is forfeit. Saves 60-80% of input-token cost on the
sliding-window agents.

### A3 — Mandatory intra-document image deduplication
R-100-125 v2 §5 + R-400-220 v2 (filename `img_{sha8}.json`). C13 invokes
`image_analyzer` exactly once per unique image hash. Skips redundant
Vision calls on repeated assets (footer logos). 20-50% reduction on
image-heavy documents at zero quality loss.

### B1 — Embeddings produced by C13, not C7
Architectural shift. R-400-220 v2 adds `02_chunks/embeddings.jsonl` to
the artifact layout. R-400-221 v2 stamps `embedding_model` +
`embedding_model_version` + `embedding_dimension` in the run manifest.
R-400-222 v2 adds `embedding: list[float]` to ChunkRich. R-400-223 v2
makes C7 `/ingest-chunks` a pure INSERT path (no embedding compute).
Aligns with R-400-207's reproducible-rebuild mandate.

### C1 — Opt-in batch API mode
R-100-125 v2 §2: `POST /analyze` accepts `urgency ∈ {interactive,
background}`. When `background`, C13 routes Phase 2 LLM calls through
Anthropic Batch API (50% discount, 1-24h latency).

### Deferred (Q-200-028)
Adaptive Phase 1 (skip tables if 0 detected; skip vision on decorative
images). Risk of false negatives outweighed gain.

### Files modified (spec session)
- `requirements/999-SYNTHESIS.md` v7 → **v8** (D-020 v1 → v2)
- `requirements/100-SPEC-ARCHITECTURE.md` v14 → **v15** (R-100-125 v1 → v2)
- `requirements/400-SPEC-MEMORY-RAG.md` v6 → **v7** (R-400-220..223 v1 → v2)
- `requirements/800-SPEC-LLM-ABSTRACTION.md` v3 → **v4** (R-800-131/132 v2 + new R-800-134)
- `requirements/CHANGELOG.md` (D-020 v2 entry)
- `requirements/060-IMPLEMENTATION-STATUS.md` (340 requirements, +1 = R-800-134)

## Part 2 — Strip (Session 2/7)

Physical removal of D-020 § "Removed modules" + tests + pyproject extras.

### Source files removed
- `src/rag/` (entire dir, 30 files — retriever/, vector_store/, graph_store/,
  embeddings/, enricher.py, indexer.py, models.py)
- `src/consolidator/` (entire dir, 4 files)
- `src/batch/` (entire dir, 4 files)
- `src/graph/` (entire dir, 24 files — exporters/, layers/, profiles/, taxonomy, merger, etc.)
- `src/cache/{redis_store,sqlite_store}.py`
- `src/llm/adapters/{anthropic,google,ollama,openrouter}_adapter.py` (kept `openai_adapter.py`)
- `src/storage/{local_writer,s3_writer}.py` (minio_writer arrives in session 3)
- `src/pipeline/agents/{community_summarizer,concept_extractor,critic,profile_generator,synthesizer,reference_extractor}.py`
- `src/pipeline/orchestrator.py` (Phase 3+4 only; `document_pipeline.py` drives Phase 1+2)
- `src/pipeline/prompts/{community_summarizer,concept_extractor,critic,entity_normalizer,profile_generator,reference_extractor,relation_normalizer,synthesizer}.txt`

**Result**: 16 609 LOC → **7 020 LOC** (`-9 589 LOC`, -58%). 160 .py → 80 .py.

### Survivors patched
| File | Change |
|---|---|
| `cache/cache_factory.py` v3 | Only `json` backend; sqlite/redis raise `ValueError`. |
| `storage/writer_factory.py` v3 | Only `minio` (session 3 lazy import); local/s3 raise `NotImplementedError`. |
| `llm/client_factory.py` v3 | Only OpenAI-compat adapter; legacy provider strings map to OpenAI. |
| `pipeline/llm_factory.py` (`_create_client`) | Collapsed branches: all providers → OpenAIAdapter. |
| `pipeline/state.py` v2 | Dropped Phase 3 fields (raw_triplets, graph, community_*, entity_profiles, synthesis, quality_score, get_graph_stats). |
| `pipeline/document_pipeline.py` v2 | Header updated: Phase 1+2 only. |
| `api/facade.py` v2 | Removed `_index_results` + `_link_to_corpus`; new `chunks_count` result field. |
| `api/models.py` v2 | `AnalysisResult` Phase 1+2 fields only. `ConfigOverrides` trimmed (no entity_similarity/community/profile/consolidator/critic). |
| `main.py` v2 | Dropped `batch` subcommand; updated result summary. |
| `config/agents.py` v2 | AGENT_REGISTRY = [summarizer, densifier]; PHASE_COMPONENT_MAP trimmed. |
| `config/settings.py` v2 | LLM stripped to OpenAI-compat (`openai_base_url`); rag_*, consolidator_*, gpu_*, vector_db_*, graph_db_*, chunk_output_mode, batch_scan_* fields removed; validator V-01..V-04 removed; cache_backend collapsed to `json`; output_writer collapsed to `minio`; new `urgency` + `embeddings_at_extractor` flags. |
| `llm/token_budget.py` v2 | `_AGENT_COST_RATIOS` Phase 1+2 only; added `decontextualizer_screener` (~60 tokens/chunk); removed Phase 3 agents. |
| `pyproject.toml` v5 → v6 | Version 0.3.1 → 0.4.0; deps trimmed (no networkx/scipy/igraph/leidenalg/anthropic/google-generativeai); extras: removed `graph`, `rag`, `storage`, `gpu`; new `pipeline` + `minio` extras. |

### Tests removed
- `tests/{unit,integration}/rag/` (entire)
- `tests/{unit,integration}/consolidator/` (entire)
- `tests/{unit,integration}/batch/` (entire)
- `tests/{unit,integration}/graph/` (entire)
- `tests/unit/llm/test_adapters.py`, `tests/integration/llm/test_int_llm_subsystem.py`
- `tests/unit/pipeline/test_unit_orchestrator.py`
- `tests/unit/pipeline/agents/test_unit_{profile_generator,concept_extractor,synthesizer_agent,community_summarizer,critic,reference_extractor}.py`
- `tests/unit/cache/test_unit_{redis,sqlite}_store.py`
- `tests/unit/storage/test_unit_s3_writer.py`
- `tests/integration/cache/test_int_cache_stores.py`
- `tests/integration/storage/test_int_storage_lifecycle.py`
- `tests/e2e/test_{func_agents,e2e_agents,e2e_facade}.py`

Tests with mixed dead/alive references (test_unit_llm_factory,
test_unit_document_pipeline, test_unit_registry, test_unit_dag_builder,
test_unit_cache_factory, test_unit_settings, test_unit_writer_factory,
tests/tracking/*, test_int_pipeline_subsystem) left intact — to be
rewritten in session 3 alongside the new design.

## Verification

- `python -m compileall ay_extractor/src` ✓ (silent, no errors).
- 0 orphan imports surviving in `src/` (grep on `from ayextractor.{rag,consolidator,batch,graph,...}` returns empty).

## Critical-partner notes

- The strip is **non-functional** at this point — running `analyze()` on a
  real document will fail because the embedding / minio writer is missing
  (session 3 deliverables). That's expected; the intermediate state is
  consistent (compileall green, no orphan imports) but not runtime-ready.
- The patched factories raise clear errors (`NotImplementedError` for
  writer, `ValueError` for cache) instead of silently failing —
  diagnosability over usability during the strip window.
- Embedding-at-C13 (B1) means C7's existing `EmbeddingProvider` is now
  duplicated by AyExtractor's future `embeddings_client.py` (session 3).
  Different abstractions for different lifecycles (C7 owns runtime
  embedder pool ; AyExtractor owns the per-doc embed pass) — accepted
  duplication, justified by D-020's code-isolation invariant.

## Next

**Session 3/7** : Refactor LLM → library where possible:
- `extraction/structure_detector.py` v2 (heuristics-only, no LLM in default path).
- New library-based reference extraction (replace the removed `reference_extractor` agent) under `extraction/reference_extractor.py`.
- New `storage/minio_writer.py` (S3-API via boto3).
- New `llm/embeddings_client.py` (OpenAI-compat `/embeddings` → C8).
- Re-wire `cache/json_store.py` and `storage/writer_factory.py` to land on MinIO.
- No HTTP wrapper yet (session 4).
- Fix the test files left dangling in session 2.

## Files modified (this session)

Spec (Part 1):
- `requirements/999-SYNTHESIS.md` (v8)
- `requirements/100-SPEC-ARCHITECTURE.md` (v15)
- `requirements/400-SPEC-MEMORY-RAG.md` (v7)
- `requirements/800-SPEC-LLM-ABSTRACTION.md` (v4)
- `requirements/CHANGELOG.md`
- `requirements/060-IMPLEMENTATION-STATUS.md` (regenerated, 340 reqs)

Code (Part 2):
- `ay_extractor/src/cache/cache_factory.py` (v3)
- `ay_extractor/src/storage/writer_factory.py` (v3)
- `ay_extractor/src/llm/client_factory.py` (v3)
- `ay_extractor/src/llm/token_budget.py` (v2)
- `ay_extractor/src/pipeline/state.py` (v2)
- `ay_extractor/src/pipeline/document_pipeline.py` (v2 header)
- `ay_extractor/src/pipeline/llm_factory.py` (patched)
- `ay_extractor/src/api/facade.py` (v2)
- `ay_extractor/src/api/models.py` (v2)
- `ay_extractor/src/main.py` (v2)
- `ay_extractor/src/config/agents.py` (v2)
- `ay_extractor/src/config/settings.py` (v2)
- `ay_extractor/pyproject.toml` (v6)
- `.claude/SESSION-STATE.md` (v58)
- `.claude/sessions/2026-05-28-d020-session2-strip.md` (this file, NEW v1)

Plus the 80+ files physically deleted (listed in Part 2).
