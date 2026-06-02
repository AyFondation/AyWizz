<!-- =============================================================================
File: 2026-06-02-c13-enrichment-activation.md
Version: 1
Path: .claude/sessions/2026-06-02-c13-enrichment-activation.md
Description: Activated the full C13 enrichment surface (document summary, term
             disambiguation, densification, image vision + dedup) that D-020
             had left scaffolded-but-dormant ; made it per-project configurable
             (C7-owned config propagated UI→C7→C12→C13) ; surfaced the outputs
             in the UI ; and corrected dev LLM routing to Claude via C8/LiteLLM.
             Multi-phase (P1→P4 + cleanup + dev recable). R-400-224.
============================================================================= -->

# Session — C13 enrichment activation + per-project config + Claude routing (2026-06-02)

## Context

After the D-020 re-partition (`2026-06-01-...`), the operator asked whether a
source upload only chunks, or also enriches (summary, image dedup, image→text,
disambiguation, …). Investigation (Explore agents + code reads) found the
enrichment **fully scaffolded but DORMANT**: the agents exist with real
prompts, but `facade.analyze` never ran them, and the manifest hardcoded
`"image_vision_enabled": True` (a lie). The operator directed: **fix it,
activate ALL options, make them entirely configurable per project (with a
default), add tests for on AND off, surface the outputs, and the image-analyzer
MODEL must be configurable independently of the text model.**

## What this session shipped (P1→P4)

### P1 — text enrichment activation (config-driven)
- **Key finding**: the agents (`summarizer`/`decontextualizer`/`densifier`)
  expect their OWN per-chunk `*Input`, NOT the global `PipelineState` — so the
  generic `PipelineRunner`/`DocumentPipeline.process` would `TypeError`. Wrote a
  dedicated orchestrator instead (`pipeline/enrichment.py`): interleaved
  decontextualize→refine-summarize per chunk, then densify once. Best-effort
  (a chunk's agent failure is recorded, never aborts the batch).
- `resolve_enrichment(quality_tier, ConfigOverrides)` — tier preset (minimal=off,
  standard=summary, high=all) OVERRIDDEN by explicit per-option flags. This is
  the seam P3's project config plugs into.
- `facade.analyze` wires it after chunking + wires deterministic **reference
  extraction** (`references.json`) + writes `refine_summary.md` / `dense_summary.md`
  + **fixed the misleading manifest** (flags reflect the resolved config).
- Decontextualization carried by `text` (decontextualized) vs `original_text`
  (original) — C7 `ChunkRich` already models this, so **no consumer contract break**.

### Cleanup — C13 test rot (operator: "no debt, clean code")
The D-020 strip had left ~20 stale tests. Fixed/removed WITHOUT test-gaming (§10):
removed graph/CLI/normalizer tests (features removed from spec, §10.2 #8);
updated renamed fields (`density_iterations`→`chain_of_density_iterations`,
default provider `anthropic`→`openai`, `resolve_all` agent set);
rewrote the facade `TestAnalyze` to drive the REAL path (no removed-DocumentPipeline
mock); fixed 2 genuine **test defects** (§10.3-B): a duplicate `"summarizer"`
dict key in `_make_session` (2nd silently overwrote the 1st) + 2
`test_multiple_agents` with contradictory asserts; converted 2 `import_error`
tests off the deprecated `get_event_loop().run_until_complete` (broke on 3.13).
**417 C13 unit green + facade.py ruff-clean** (also fixed its 7 pre-existing
UP017/F841/E501 violations since it was heavily edited).

### P2 — images: bytes + sha256 dedup + vision caption
- `extraction/image_pipeline.py` (NEW): `extract_image_blobs` pulls real bytes
  (PDF `doc.extract_image(xref)`, DOCX `rel.target_part.blob`);
  `caption_image_blobs` **deduplicates by sha256** (identical images captioned
  ONCE), captions each unique image via `analyze_image`, writes a per-image
  artifact `01_extraction/images/img_{sha8}.json`, folds descriptions back into
  `ExtractionResult.images`. The vision model is resolved via
  `llm_factory("image_analyzer")` — **INDEPENDENT** of the text agents
  (`llm_image_analyzer` / `llm_assignments`).
- Config flag `image_vision` (tier preset standard+ ; override via
  `ConfigOverrides.image_vision_enabled`). Tests prove dedup (3 occurrences → 2
  vision calls), caption fold, failure-isolation, independent model resolution.

### P3 — per-project enrichment config (UI→C7→C12→C13)
- **Decision: C7 owns the config** (not C2) — the ingestion consumer reads it
  locally at trigger time, no C7→C2 runtime call. New `EnrichmentConfig` model +
  `memory_project_config` Arango collection + `GET` (authenticated) / `PUT`
  (owner/admin, tenant_manager excluded) endpoints. `to_config_overrides()` maps
  it to the C13 contract (incl. `llm_assignments["image_analyzer"]` for the
  independent image model).
- `store_raw_upload_and_trigger` now reads the project config and forwards
  `quality_tier` + `config_overrides` in the C12 webhook payload. The n8n
  workflow (`extract_and_ingest.json`) forwards `config_overrides` to C13
  `/analyze` (which already accepts it via `_build_overrides`).
- UI: `EnrichmentConfig` type + `getEnrichmentConfig`/`updateEnrichmentConfig`
  (apiClient v11) + a Settings "Ingestion enrichment" section (tier select +
  tri-state per-option toggles + independent image-model field, owner-gated).
- Catalog +2 endpoints → 111 ; `065-TEST-MATRIX.md` + `backend-routes.json`
  regenerated. Propagation proven (project config `high` + image model →
  webhook `quality_tier=high` + `llm_assignments`).

### P4 — surface the enrichment outputs
- Chunk viewer: decontextualization **before/after** (badge + "Original (before
  disambiguation)" when `original_text != content`).
- `EnrichmentDigest` in the per-run artifact browser: renders the **document
  summary** (dense→refine) inline + the **image captions** (the heavy artifacts
  stay downloadable via the existing per-run file browser/zip).

### Dev recable — Claude, not Ollama
The operator flagged that the platform uses **Claude via C8/LiteLLM** (per the
documented preference), not Ollama. My initial dev override had pointed C13
enrichment at Ollama (mirroring the existing C13-embeddings-on-Ollama pattern).
Corrected:
- **C13 chat (enrichment) → C8/LiteLLM → Claude** (`claude-sonnet-midtier` text +
  image-analyzer ; `claude-opus-flagship` is the vision-tagged route if needed).
- **Embeddings stay on Ollama** (`all-minilm`, free ; LiteLLM carries no
  embeddings model). Required a small C13 change: **split `embedding_base_url`/
  `embedding_api_key`** from the chat `openai_*` (they target different
  providers).
- `e2e_stack.sh` v10: passes `.env.secret` as a compose `--env-file` so C13
  interpolates `C8_GATEWAY_API_KEY` (→ its `OPENAI_API_KEY` to LiteLLM), and
  **restarts c12 after the workflow seed** (n8n only reloads an imported
  workflow on restart) — both docker calls kept INSIDE the wrapper (§5.3, per
  operator's correction of a raw `docker restart`).
- Devcontainer: declared the previously-undeclared C13 deps (scikit-learn,
  ebooklib, beautifulsoup4) + editable-install `ay_extractor[all,dev,integration]`
  (Dockerfile v1.11) so the C13 suite runs in the devcontainer.

## Decisions (beyond specs)
- **Enrichment config home = C7** (ingestion owner), not the C2 project row —
  avoids a C7→C2 runtime fetch; the Settings page aggregates 2 sections
  (system_prompt=C2, enrichment=C7).
- **Image-analyzer model independent** of the text model (R-400-224) — its own
  `llm_assignments["image_analyzer"]` entry, settable per project.
- **Vision/dedup enabled on standard+** tiers (any non-minimal turns the
  options on), overridable per option.
- **Dev C13 enrichment → Claude via C8** (operator preference) ; embeddings stay
  Ollama via the new base-url split.

## Verification
- **C13**: 417 unit green, ruff-clean (full suite now runs in the devcontainer
  after the dep + editable-install fix).
- **Backend** (`run_tests.sh ci`): ruff + mypy + pytest **All stages OK** (incl.
  the C7 config integration test + propagation unit tests).
- **UI** (`npm run ci`): lint + typecheck + **372 tests** + coverage 4/4.
- **Live**: stack rebuilt via `e2e_stack.sh dev` (UI P4 + C13 recable + workflow
  re-import + c12 restart via the wrapper). Enrichment runs against Claude **iff**
  `.env.secret` carries `C8_GATEWAY_API_KEY` + `ANTHROPIC_API_KEY`; otherwise
  best-effort (chunks still index via Ollama embeddings). End-to-end Claude
  enrichment with real keys is operator-driven (paid).

## Follow-ups
- Validate the live end-to-end Claude enrichment on a real PDF/DOCX with valid
  `.env.secret` keys → assert summary/captions populated (and watch cost: high
  tier = many Claude calls per upload).
- Decontextualization **screener** (the planned Haiku gate, `llm_decontextualizer_screener`)
  is NOT implemented — decontextualization runs without it.
- Per-chunk references stay `[]` in `chunks.jsonl`; references are document-level
  in `references.json` (per-chunk wiring deferred).
- C13 internal `# vN` file-version headers not bumped per phase (vendored
  sub-project convention; noted).
- `extract_image_blobs` byte extraction is exercised by the wiring but unit
  tests cover the dedup/caption logic on synthetic blobs; a real-PDF integration
  test is a follow-up.

## References
- Prior: `sessions/2026-06-01-d020-repartition-byte-custody-c7.md`,
  `2026-05-28-d020-ayextractor-c13-spec.md`, `2026-05-22-c8-litellm-proxy-...md`.
- Memory: `feedback_llm_routing_claude_not_ollama` (Claude via C8, not Ollama).
- CLAUDE.md §8.1 (spec-first), §8.4 (contract), §13 (auth-matrix), §10
  (test-debug), §4.6 (env-tiers), §5.3 (wrapper pattern), §5.7 (shell).
