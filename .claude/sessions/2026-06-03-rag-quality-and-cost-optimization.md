<!-- =============================================================================
File: 2026-06-03-rag-quality-and-cost-optimization.md
Version: 1
Path: .claude/sessions/2026-06-03-rag-quality-and-cost-optimization.md
Description: A large multi-theme session on the C13→C7 RAG pipeline: (1) two chat
             display fixes ; (2) contextual-retrieval rework (self-contained
             chunk, context-aware embedding + contextual BM25, summary de-dup,
             full chunk display, RAG de-truncation) ; (3) structure-aware
             chunking (option B) ; (4) a legacy-test/dead-code audit ; (5) a
             four-lever ingestion COST optimisation incl. a map-reduce
             summariser redesign + a reusable prompt-cache primitive + progress
             observability. R-400-222/223 touched ; C8 routing extended.
============================================================================= -->

# Session — RAG quality + ingestion cost optimisation (2026-06-03)

## Context

Operator working through the C13 enrichment surface activated 2026-06-02. The
session chained several distinct asks, each validated before implementation.

## 1. Chat display fixes

- **Composer kept the just-sent prompt.** Race: `onSend` clears the composer,
  the *restore* effect runs BEFORE the *persist* effect on the post-send
  re-render, reads the still-stale draft and re-injects it. Fix: `onSend` marks
  the draft handled (`composerRestoredRef`) + clears the persisted draft.
  Regression test added (the old test missed it — the mock `setDraft` didn't
  reflect the store).
- **Send button overflowed.** The chat `<main>` lacked the `mx-auto max-w-5xl`
  the page's other states use → constrained. (`[cid]/page.tsx` v17.)

## 2. Contextual retrieval — self-contained chunk (validated model)

Operator's model: the chunk is self-contained ; the embedding captures ALL
relevant data WITHOUT duplicating it ; the display shows everything fed to the
LLM + structure + extra metadata.

- **Context-aware embedding** (C13 `facade._embedding_text`): embed
  `section_path + content` (a TRANSIENT augmented text), not content alone.
  **`global_summary` and the cumulative `context_summary` are excluded** — being
  (near-)identical across chunks they would collapse vector discrimination
  toward the document centroid (honest deviation from the literal
  "context_summary" first discussed — it turned out to be a cumulative refine
  summary, not a per-chunk situating blurb).
- **Contextual BM25** (O1's twin): persist that augmented text as `search_text`
  and index it in the ArangoSearch view (alongside `content` for back-compat) →
  BOTH retrieval arms become contextual (the Anthropic pattern where the gain
  actually lives). `lexical_search` SEARCHes both fields.
- **Summary de-duplication**: `global_summary` removed from every chunk's
  metadata ; stored ONCE on the source row (`document_summary`), referenced at
  display time.
- **Full chunk display**: `ChunkContent` gained `search_text`, `content_hash`,
  `embedding_model/dim`, `extraction_run_id`, references/images/tables,
  `document_summary` ; the UI chunk viewer renders the retrieval text + a
  metadata grid + the shared summary (sources/[sid] v6).
- **RAG de-truncation** (c3): `_format_retrieved_chunks` fed the LLM only 800
  chars — severing the very context that makes a chunk self-sufficient. Now
  whole + a `section:` prefix (chunks are already bounded by `chunk_token_size`).

## 3. Structure-aware chunking (option B)

Diagnosis: `structure.json` was a flat heading-list with `end_position` = end of
the heading LINE (not the section body) ; the `StructuralChunker` packed
paragraphs to ~2000 chars CROSSING section boundaries and tagged `section_path`
by a fragile substring match → unreliable, which also weakened the new
contextual embedding.

- **`structure_detector`**: running-cursor offsets (fixed the `text.find`
  repeated-heading bug) ; each section now spans up to the NEXT heading = its
  real body extent.
- **`StructuralChunker` v2**: slice text per section, then size-pack WITHIN a
  section ; **option B** — small sibling subsections sharing the same immediate
  parent coalesce up to the target (never crossing a parent) ; `section_path` =
  the longest-common-ancestor chain (reliable, hierarchical) ; oversized
  sections sub-chunked ; no-structure fallback preserved.
- Integration fixture switched from hand-typed offsets to the REAL
  `detect_structure(LONG_TEXT)` (the chunker now slices on those offsets).

## 4. Legacy / dead-code audit (operator-requested)

- **7 pre-existing C13 integration failures** (never run by the unit-only CI)
  all referenced D-020-removed symbols — fixed to the real contract (§10.4-D):
  `AnalysisResult` (no `community_count`/`graph_path`/`themes`),
  `ConfigOverrides` (`density_iterations`→`chain_of_density_iterations`),
  `Settings` (provider default `openai` ; removed `rag_*` validator replaced by
  a test of the surviving V-05 rule), tracking (a mislabelled `summarizer`
  record → `densifier`).
- **Dead code removed** (operator-approved, §11.2): `pipeline/document_pipeline.py`
  + `pipeline/runner.py` (`DocumentPipeline`/`PipelineRunner`) — referenced only
  in comments on the live path + their own two tests ; deleted with the tests,
  stale references cleaned.
- Trees otherwise clean (an Explore sweep mis-reported the UI tree as empty —
  it has 373 healthy tests).

## 5. Ingestion COST optimisation — four levers

A real PDF (1.1 MB) cost **$4.53**. Root cause: in `standard` tier, a per-chunk
refine summary + per-image vision, BOTH on **Sonnet** (C13 agents fell through
to `default: claude-sonnet-midtier`).

- **#1 Model tiering → Haiku** (biggest, ~73 %): `agent_routes` +
  `litellm-config` mark Haiku `vision`+`prompt_caching` ; C13 dev env
  `LLM_DEFAULT_MODEL`/`LLM_IMAGE_ANALYZER` → `claude-haiku-fast`. Haiku 4.5 is
  quality-equivalent for descriptive leaf work.
- **#2′ Vision trim**: caption `max_tokens` 2048→512 (output is the dear axis) +
  **skip decorative images** <3072 bytes. (Client RESIZE dropped — Anthropic
  already downscales >1568px server-side and bills the reduced size, so it
  saves nothing ; Pillow not even installed.)
- **#3 Map-reduce summariser redesign**: the summariser agent is now STATELESS
  (`text→summary`, schema + prompt rewritten). Orchestrator: **MAP** (chunk
  batches of 5, concurrent, bounded 8) → ~5× fewer calls + parallel ;
  **REDUCE** (hierarchical fan-in 10) → document summary ; decontextualisation
  ALSO parallelised, now using the document summary as context. Replaces the
  sequential quadratic refine. Agent + orchestrator tests rewritten.
- **#4 Reusable prompt-cache primitive**: `cache_system` passthrough on the C13
  `OpenAIAdapter` + `cache_hint="static"` → `cache_control: ephemeral` injection
  in the platform C8 client (`_apply_static_prompt_cache`) → available to ANY
  caller (C3 chat etc.). Honest finding: it is a **no-op for the enrichment
  agents** (their system prompt is ~10 tokens, far below the Haiku 4096 / Sonnet
  1024 minimum) — built for future big-prefix callers, not sprinkled where it
  can't fire.
- **Observability**: per-phase progress logs (`enrichment.map/reduce/
  decontextualize/densify`) → visible live via `e2e_stack.sh logs c13-extractor`.
- **Wrapper**: new `e2e_stack.sh restart-llm` (recreate c13 for the env change +
  restart litellm to reload its mounted config ; §5.3) — applied live, Haiku
  confirmed in the container env.

Expected: **$4.53 → ~$1** + much faster (parallelism). Live in dev via the c13
bind-mount + `--reload`.

## Verification

- **C13**: 548 unit+integration green + e2e chunking golden intact + ruff clean
  on every touched file.
- **Backend** (`run_tests.sh ci`): ruff + mypy + pytest **All stages OK**
  (incl. the new `_apply_static_prompt_cache` tests).
- **UI** (`npm run ci`): lint + tsc + **373 tests** + coverage 4/4.

## Decisions (beyond specs)

- Embedding/BM25 augment with **section_path only** (not global/cumulative
  summaries) — discrimination over completeness.
- `global_summary` stored **once** on the source row, not per chunk.
- Chunking **option B** (sibling-subsection coalescing under a shared parent).
- C13 enrichment + image analysis routed to **Haiku** (quality-equivalent, ~4×
  cheaper) ; densifier MAY be promoted to Sonnet for premium high-tier summaries.
- Prompt caching is a **reusable primitive**, opt-in, no-op below threshold —
  deliberately NOT wired into the tiny-prompt enrichment agents.

## Follow-ups

- Measure the real post-optimisation cost on a fresh upload (operator).
- UI inline progress (status.json progress fields → the "live processing" area)
  — logs cover it for now ; the UI surface is a separate enhancement.
- Wire `cache_hint="static"` in C3 chat (the genuine caching beneficiary: large
  reused system prompt + context across turns).
- D-020.5 batch API (`urgency=background`) still spec-only — would stack on the
  Haiku saving.

## References

- Prior: `2026-06-02-c13-enrichment-activation.md`.
- Memory: `feedback_llm_routing_claude_not_ollama`, `project_ui_coverage_initiative`.
- Anthropic prompt-caching doc (operator-provided) ; CLAUDE.md §10/§11/§12 (test
  discipline), §5.3 (wrapper), §4.6 (env tiers), §9 (this entry).
