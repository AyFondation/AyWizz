<!-- =============================================================================
File: 2026-05-28-d020-ayextractor-c13-spec.md
Version: 1
Path: .claude/sessions/2026-05-28-d020-ayextractor-c13-spec.md
Description: Session journal — D-020 adoption (AyExtractor as new dependency
             component C13) + file-manager UI tranche closure. Spec-only
             session for D-020 ; UI tranche shipped in the same chat.
============================================================================= -->

# Session — D-020 : AyExtractor adopted as dependency C13 (2026-05-28)

## Context

Operator's directive (verbatim): *"je veux qu on traite le traitement des
sources via n8n, je ne veux pas que cela passe par autre chose"* +
*"AyExtractor est un module externe ... integre de maniere efficiente au
workflow de AyWizz ... fait peu de choses mais les fait tres tres bien, sans
recourt à l'utilisation massive de LLM, si ça peut être fait via du code /
une librairie python alors on privilégie la réduction de coût"*.

Two iterations were needed to converge: my first proposal folded AyExtractor
as an internal service C13 — operator pushed back (AyExtractor is EXTERNAL,
zero code coupling, file-based contract). Second iteration produced the
final shape — C13 = dependency component, MinIO artifacts as contract, LLM-
frugal defaults — validated four-by-four via `AskUserQuestion`. Operator
approved with "ok go".

## What this session shipped

**Spec only — no `.py`, no workflow, no test touched** (D-020 is a 7-session
plan ; this is session 1/7).

### D-020 in `999-SYNTHESIS.md` (v6→v7)
- New decision entity `D-020` recording: AyExtractor adopted as dependency
  C13, scope locked to Phase 1+2 (extract + chunk), re-partition of D-013
  pipeline (C12 trigger → C13 extract+chunk → C7 embed+index), LLM-frugal
  stance, mandatory strip of 10+ modules duplicating AyWizz stack, vendored
  monorepo + local wheel build, C8 routing via `OPENAI_BASE_URL` env switch
  (no AyExtractor code change), 7-session implementation roadmap.
- D-018 was already taken (intent/evidence MinIO layer, v6) — D-019 too
  (bi-temporal KG) — hence D-020 not D-018.

### `100-SPEC-ARCHITECTURE.md` (v13→v14)
- `R-100-081 v1→v2` — re-partitions D-013 ingestion ownership across
  C12 + C13 + C7. Supersedes the v1 "C12 owns parsing".
- `R-100-125` (NEW) §10.9 — C13 declaration: HTTP-only surface
  (`POST /analyze`, `GET /status/{run_id}`, `GET /healthz`), vendoring +
  versioning rules (wheel stamp in `run_manifest`), C8 routing, LLM
  frugality (5 invariants), storage isolation (MinIO writer), 10+
  removed modules list, failure handling, resource limits.
- §4.2 component table — C13 added under "dependency" type (4 deps now:
  C10/C11/C12/C13).

### `400-SPEC-MEMORY-RAG.md` (v5→v6) — §4.3bis new
- `R-400-020 v1→v2` — three-step pipeline becomes four-step
  (upload → extract+chunk[C13] → index[C7] → optional KG[C7]).
- `R-400-021 v1→v2` — format list extended to PDF/EPUB/DOCX/MD/TXT +
  PNG/JPG/JPEG/WEBP ; ownership moved to C13.
- `R-400-022 v1→v2` — chunking becomes structure-aware with fixed-window
  fallback ; ownership moved to C13.
- `R-400-220` (NEW) — MinIO artifact layout
  (`{bucket}/{tenant}/{project}/{source}/runs/{run_id}/`
  with `00_metadata/`, `01_extraction/`, `02_chunks/`, `status.json`).
- `R-400-221` (NEW) — `RunManifest` schema (Pydantic-serialisable),
  ayextractor_version + git sha + per-agent llm_assignments + prompt_hashes +
  per-phase timing + token counts + cost. Byte-stable for an unchanged
  input → enables run diff.
- `R-400-222` (NEW) — `ChunkRich` schema: `text` (= decontextualised if
  tier=high), `original_text?`, `context_summary?`, `global_summary?`,
  section_path, char offsets, references / images / tables, token_count,
  extraction_run_id. C7's retrieval response SHALL include the extra
  fields.
- `R-400-223` (NEW) — C7 endpoint `POST /memory/projects/{pid}/sources/
  {sid}/ingest-chunks` (rich chunk shape, quota-enforced, project_editor+).
- `R-400-224` (NEW) — per-project `quality_tier ∈ {minimal | standard |
  high}`, default `minimal` (zero LLM cost when no images).
- `R-400-225` (NEW) — resume-from-phase semantics (carried_from / fresh
  phase manifests, no LLM re-spend on resume).

### `800-SPEC-LLM-ABSTRACTION.md` (v2→v3)
- §4.6 catalog extended with 4 AyExtractor agents.
- `R-800-130` — `ayextract.image_analyzer` (vision-capable mid-tier,
  mandatory when images present).
- `R-800-131` — `ayextract.decontextualizer` (Haiku fast, prompt
  caching, opt-in `high` tier only).
- `R-800-132` — `ayextract.summarizer` (Sonnet mid, prompt caching,
  `standard` + `high`).
- `R-800-133` — `ayextract.densifier` (Sonnet mid, long context +
  prompt caching, `high` only).
- §8.1 sample `agent_routes:` extended accordingly.

### Side-deliverables
- `requirements/CHANGELOG.md` — full session entry (Added / Changed /
  Deprecated / Notes blocks).
- `requirements/060-IMPLEMENTATION-STATUS.md` regenerated via
  `audit_implementation_status.py --write` (339 requirements indexed,
  new R-100-125 + R-400-220..225 + R-800-130..133 marked `not-yet`,
  expected).
- `.claude/SESSION-STATE.md` v56→v57 — §1, §3 (added D-020 + file-manager
  DONE), §4 (added Q-200-022..025), §5 (next = session 2/7 strip), §6
  prepended.

## File-manager UI tranche closure (same chat, separate concern)

Operator's earlier turn "ok go pour le développement de ce qui a été
planifié" + AskUserQuestion answers (BOTH Documents + Working area, 3 gaps =
root/empty-state + content editor + blank-file) was completed in this
chat **before** D-020. Recorded here for completeness:

- `ay_platform_ui/components/live-docs-manager.tsx` v2 (NEW, ~330 LOC)
  — shared LiveDocsManager (FileTree + context menu + toolbar + empty-state
  + content editor + drag-drop move). `apiClient` deps memo'd, biome
  `useExhaustiveDependencies` clean.
- `ay_platform_ui/app/(protected)/projects/[pid]/artifacts/page.tsx` v2 —
  profile-conditional branch renders LiveDocsManager for docgen profile.
- `ay_platform_ui/app/(protected)/projects/[pid]/working-area/page.tsx`
  v10 — 3 gaps added live-docs-only: toolbar `+ New file`/`+ New folder`,
  root empty-state, inline `Edit`/`Save`. `_LIVE_DOCS_ACTIONS` gained the
  `newFile` action. Multi-run / history viewer / chat sidebar preserved.
- `ay_platform_ui/lib/apiClient.ts` + `lib/types.ts` — `DocumentRef` type
  + `getDocumentText` / `createDocument` / `updateDocument` methods.
- Tests : 5 new unit tests on the apiClient methods + 3 integration
  tests on LiveDocsManager (empty-state, blank-file POST). `npm run test`
  = **131/131 green** ; `lint` + `typecheck` verts.
- Build : `npm run build` panics on Turbopack symlink (devcontainer-
  specific env issue, not a code defect) — flagged, non-blocking.

## Critical-partner notes (§1.1)

- First-pass proposal mis-scoped AyExtractor as an INTERNAL service — wrong.
  Operator correctly re-anchored on "external dependency, file-based
  contract, code-isolated". Re-cast as Pattern B macro-orchestration
  (4-node n8n) instead of the literal Pattern A (15-node) ; recommended +
  approved.
- LLM frugality is the operator's hard constraint. AyExtractor as
  delivered (the prior internal work) called LLM aggressively for
  `structure_detector` and `reference_extractor` — D-020 mandates rewriting
  these as deterministic libraries (regex + `refextract`) + opt-in fallback.
  This is the main internal refactor work in sessions 2-3.
- The `quality_tier=minimal` default ensures ingestion is FREE on text-only
  documents — matching the legacy C7 path cost. Operator opts in to
  better quality on a per-project basis. Cost containment by construction.

## Next

**Session 2/7 (next):** physical strip of dead modules in `ay_extractor/
src/`. Modules to delete (no `extras` flag, no commented code):
- `rag/{retriever,vector_store,graph_store,enricher,indexer,embeddings}/`
- `consolidator/`, `batch/`, `graph/`
- `pipeline/agents/{concept_extractor, community_summarizer,
  profile_generator, synthesizer, critic}.py`
- `cache/{sqlite,redis,arangodb}_cache_store.py`
- `llm/adapters/{anthropic,google,openrouter}_adapter.py`
- `storage/{local_writer,s3_writer}.py`

Tests under `ay_extractor/tests/{unit,integration}/` mirroring these
modules are trimmed in lock-step. Estimated diff: ~8000 LOC removed, 0
new code, 0 platform code touched.

**Sessions 3-7:** see D-020 operationalisation list in `999-SYNTHESIS.md` §5.

## Files modified (this session)

- `requirements/999-SYNTHESIS.md` (v7)
- `requirements/100-SPEC-ARCHITECTURE.md` (v14)
- `requirements/400-SPEC-MEMORY-RAG.md` (v6)
- `requirements/800-SPEC-LLM-ABSTRACTION.md` (v3)
- `requirements/CHANGELOG.md`
- `requirements/060-IMPLEMENTATION-STATUS.md` (regenerated)
- `.claude/SESSION-STATE.md` (v57)
- `.claude/sessions/2026-05-28-d020-ayextractor-c13-spec.md` (this file, v1)

UI tranche files (closed earlier in same chat, see file-manager section):
- `ay_platform_ui/components/live-docs-manager.tsx` (v2)
- `ay_platform_ui/app/(protected)/projects/[pid]/artifacts/page.tsx` (v2)
- `ay_platform_ui/app/(protected)/projects/[pid]/working-area/page.tsx` (v10)
- `ay_platform_ui/lib/apiClient.ts`, `lib/types.ts`
- `ay_platform_ui/tests/unit/lib/apiClient.test.ts` (v3, +5 tests)
- `ay_platform_ui/tests/integration/live-docs-manager.test.tsx` (v1, NEW, 3 tests)
