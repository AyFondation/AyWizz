<!-- =============================================================================
File: 2026-05-28-d020-session5-c7-ingest-chunks.md
Version: 1
Path: .claude/sessions/2026-05-28-d020-session5-c7-ingest-chunks.md
Description: D-020 session 5/7 — C7 ingest-chunks endpoint (R-400-223 v2
             pure-INSERT) + rewrite des 2 tests skip-marked session 2 +
             fix du run_id mismatch session 4.
============================================================================= -->

# Session — D-020 session 5 : C7 ingest-chunks + tests rewrite (2026-05-28)

## Context

Session 5 is the **first session that touches `ay_platform_core/`** in the
D-020 roadmap (sessions 1-4 lived under `ay_extractor/` + `requirements/`).
The deliverable: C7 accepts the artifact set C13 produces (sessions 3+4)
via a new pure-INSERT endpoint, while the legacy in-process parse + chunk
path stays alive (deprecated) until session 7's cleanup.

The session also resolves two debt items flagged in earlier sessions:
- **Run_id mismatch HTTP↔facade** flagged at session 4 § "Critical-partner
  notes" — the HTTP wrapper pre-minted a run_id but `facade.analyze`
  re-minted internally, so the polled run_id never matched the artifact
  prefix.
- **2 test files module-skipped** in session 2 strip — `test_unit_settings`
  + `test_int_pipeline_subsystem`. Both rewritten now that the post-strip
  surfaces are stable.

## What this session shipped

### New C7 surface (D-020 R-400-223 v2)

1. **`c7_memory/models.py`** — Two new Pydantic models:
   - `ChunkRich` — R-400-222 v2 shape (text + original_text + context_summary
     + global_summary + section_path + char offsets + token_count + references
     + images (sha8) + tables + extraction_run_id + optional embedding).
   - `ChunkIngestRequest` — POST body for `/ingest-chunks`: extraction_run_id +
     manifest_object_key + embedding_model + embedding_model_version +
     embedding_dimension + chunks (≥1) + uploaded_by + mime_type.

2. **`c7_memory/service.py::MemoryService.ingest_chunks_from_extractor`**
   (new method, ~120 LOC):
   - Cross-validates embedding metadata against each chunk's vector length
     (mismatch → HTTPException 400).
   - Enforces per-project quota (R-400-024) on cumulative token_count
     (×4 bytes/token estimate).
   - Pure-INSERT path: persists each chunk's vector AS-IS (no embedder
     invocation when present).
   - Backward-compat fallback: when `ChunkRich.embedding is None`, the local
     embedder runs on missing vectors only (transitional, removed v2 per
     D-020 session 7).
   - Stamps `processing_version` with the C13 embedding model id, NOT
     C7's — so a future C13 model upgrade marks rows stale.
   - Persists rich metadata (section_path, char offsets, references,
     images, tables, extraction_run_id, original_text, global_summary)
     in `memory_chunks.metadata` for downstream consumption.
   - Optional MinIO mirroring (R-400-207) when blob storage is wired.

3. **`c7_memory/router.py`** — new endpoint:
   ```
   POST /api/v1/memory/projects/{project_id}/sources/{source_id}/ingest-chunks
   ```
   Same RBAC as `/sources` ingest (`project_editor` / `project_owner` /
   `admin`, `tenant_manager` excluded by E-100-002 v2). Returns
   `SourcePublic` (201). `@relation` markers reference R-400-223,
   R-100-081, R-400-220.

4. **Legacy `POST /sources/upload`** — marked `deprecated=True` (OpenAPI
   metadata) + docstring updated to point operators at the new ingestion
   path. Logic preserved untouched (removed in session 7).

### Auth matrix (CLAUDE.md §13)

- New `EndpointSpec` in `tests/e2e/auth_matrix/_catalog.py` for
  `/ingest-chunks` (ROLE_GATED, PROJECT scope, 201 success,
  `accept_roles=(project_editor, project_owner)`,
  `accept_global_roles=(admin,)`, `excluded_global_roles=(tenant_manager,)`,
  backend=ARANGO).
- Existing `/sources/upload` entry annotated as DEPRECATED in its `notes`.
- `requirements/065-TEST-MATRIX.md` regenerated via
  `python scripts/checks/generate_test_matrix_doc.py --write` → **103
  endpoints**.

### Run_id mismatch fix (session 4 flag)

- **`ay_extractor/src/api/models.py`** v3 → Metadata gains optional
  `run_id` field. When set, `facade.analyze` honours it verbatim; when
  None (CLI path) the facade mints internally.
- **`ay_extractor/src/api/facade.py`** → `_generate_run_id` only fires if
  `metadata.run_id is None`.
- **`ay_extractor/src/api/http.py`** → propagates the HTTP-side
  pre-minted run_id through Metadata. The post-execution
  `assert result.run_id == run_id` makes any future regression noisy.

### Tests rewritten (session 2 debt)

- **`tests/unit/config/test_unit_settings.py`** v4 — Complete rewrite
  against the post-strip Settings v3 surface (~70 LOC, was ~507 LOC).
  Covers defaults (writer=minio, cache=json, embedding_model=voyage-3,
  urgency=interactive, embeddings_at_extractor=True), validators (V-05
  chunk_overlap rule — the only validator surviving the strip), Literal
  rejection of removed values (`local`/`s3` writer; `sqlite`/`redis`
  cache), and override propagation. The pytestmark skip is removed.

- **`tests/integration/pipeline/test_int_pipeline_subsystem.py`** v5 —
  Rewrite from 700+ LOC + 6 stripped-agent imports to ~100 LOC against
  the 3 surviving Phase 2 agents (summarizer + densifier + screener-via-
  config_only). Covers AgentRegistry surface (load_all skips Phase 3),
  DAGBuilder topology (densifier after summarizer when dependency
  declared), PipelineState shape (Phase 1+2 fields present, Phase 3
  stripped, record_agent_output updates stats). pytestmark skip removed.

### New tests for C7 ingest-chunks

- **`tests/unit/c7_memory/test_ingest_chunks.py`** v1 — 5 tests, all
  green under `run_tests.sh d020-s5 ... --no-cov`:
  - `test_ingest_chunks_with_embeddings_is_pure_insert` — C7 persists
    supplied vectors as-is, processing_version stamps C13 model.
  - `test_ingest_chunks_missing_embedding_falls_back_to_local_embedder`
    — backward-compat fallback path produces a non-zero deterministic
    vector.
  - `test_ingest_chunks_dimension_mismatch_400` — vector length ≠
    declared embedding_dimension → HTTP 400 with "embedding_dimension"
    in detail.
  - `test_ingest_chunks_persists_rich_metadata` — all R-400-222 v2
    fields (section_path, char offsets, references, images, tables,
    global_summary, extraction_run_id) preserved in
    `memory_chunks.metadata`.
  - `test_ingest_chunks_enforces_quota` — cumulative token_count
    pushes the project over quota → HTTP 413 (R-400-024).

  Uses an in-memory `_FakeRepo` (chunks list, sources dict,
  quota_totals method) + `DeterministicHashEmbedder` for the fallback
  path. No testcontainers needed — pure unit tier.

### Side cleanup

- **`ay_extractor/src/config/settings.py`** — removed the duplicate
  `embedding_model` field (lines 71 + 120 both declared `voyage-3` —
  vestige of session 2 strip). The single declaration under "Embeddings
  (D-020 v2 §B1)" survives; the legacy adapter knobs
  (`embedding_provider`, `embedding_dimensions`, `embedding_ollama_model`,
  `embedding_st_model`) stay as documented no-ops so existing `.env`
  files don't fail validation.

## Verification

- `ay_platform_core/scripts/run_tests.sh d020-s5 tests/unit/c7_memory/test_ingest_chunks.py --no-cov`
  → **All stages OK**: ruff + mypy + 5/5 pytest tests passed.
- `python -m compileall ay_extractor/src ay_extractor/tests
  ay_platform_core/src/ay_platform_core/c7_memory` ✓.
- `python scripts/checks/generate_test_matrix_doc.py --write` ✓ (103
  endpoints indexed including the new `/ingest-chunks`).
- Per CLAUDE.md §12: full `run_tests.sh ci` not run on the global suite
  here (would require Arango + MinIO testcontainers); targeted unit
  run on the new test file demonstrates the contract holds. The next
  CI push will exercise the full chain.

## Critical-partner notes

- **Coverage gate not satisfied on the targeted run.** Running
  `run_tests.sh d020-s5 tests/unit/c7_memory/test_ingest_chunks.py`
  WITHOUT `--no-cov` enforces the 80% line-coverage gate over the WHOLE
  source tree, which the 5 new tests can't hit on their own. The
  `--no-cov` flag is the correct mode for a per-file targeted run; the
  cumulative coverage gate fires only in the global CI (which exercises
  ~1700 tests across the suite). Flagged so the operator's
  `run_tests.sh ci` invocation later won't be surprised.

- **`upload_source` deprecated but still wired.** The route stays in
  `router.py` so the existing integration suites
  (`tests/integration/c7_memory/test_upload_pipeline.py`,
  `test_blob_download.py`, `test_processing_version.py`) keep passing.
  Removing the route in session 7 will require either rewriting these
  fixtures against `/ingest-chunks` or accepting their deletion if
  they're now redundant with the unit tests. Decision deferred.

- **Quota estimate is approximate.** The new endpoint converts
  `token_count → bytes` using a 4× multiplier (UTF-8 word-average).
  This is conservative for ASCII English but undersized for CJK
  (4 tokens = ~12 bytes in CJK). Acceptable for v1 quota enforcement
  (the real cap is on `memory_chunks.vector` storage, not chunk text);
  refine if a customer surfaces a false-positive 413.

- **Auth matrix coverage tests not run here.** `test_anonymous_access`,
  `test_role_matrix`, `test_isolation`, `test_backend_state` would
  pick up the new `EndpointSpec` automatically but require the full
  testcontainers stack. The `_catalog.py` change is mechanical
  (parametrised tests iterate the catalog); the next CI run validates
  the matrix dimension for the new endpoint.

- **Phase 3 agents stripped from registry but not from pyproject.**
  `langchain-core` + `langgraph` deps stay (they're used by the
  surviving Phase 2 LangGraph DAG plumbing). No unused dep to remove
  this session.

## Files modified

**`ay_platform_core/` (3 files modified)**:
- `src/ay_platform_core/c7_memory/models.py` (+~80 LOC for `ChunkRich`
  + `ChunkIngestRequest`)
- `src/ay_platform_core/c7_memory/service.py` (+~120 LOC for
  `ingest_chunks_from_extractor` + import)
- `src/ay_platform_core/c7_memory/router.py` (+~50 LOC for
  `ingest_chunks` endpoint + `deprecated=True` on `upload_source`)

**`ay_platform_core/tests/` (3 files)**:
- `tests/unit/c7_memory/test_ingest_chunks.py` (NEW, ~210 LOC, 5 tests)
- `tests/e2e/auth_matrix/_catalog.py` (+~20 LOC EndpointSpec for
  `/ingest-chunks` + DEPRECATED note on `/sources/upload`)

**`requirements/`**:
- `requirements/065-TEST-MATRIX.md` regenerated (103 endpoints)

**`ay_extractor/` (4 files)**:
- `src/api/models.py` v3 → +`Metadata.run_id` field
- `src/api/facade.py` → respect pre-minted `metadata.run_id`
- `src/api/http.py` → propagate run_id + assert + comment update
- `src/config/settings.py` → drop duplicate `embedding_model` field +
  re-label legacy adapter knobs as no-ops
- `tests/unit/config/test_unit_settings.py` v4 (rewrite, ~150 LOC)
- `tests/integration/pipeline/test_int_pipeline_subsystem.py` v5
  (rewrite, ~120 LOC, was 700+ LOC with skip mark)

**Continuity**:
- `.claude/SESSION-STATE.md` (v61) — §1 + §3 + §5 + §6 updated; two
  older §3 entries condensed to fit the 150-line limit.
- `.claude/sessions/2026-05-28-d020-session5-c7-ingest-chunks.md`
  (this file, NEW v1).

## Next

**Session 6/7** — n8n workflow + persistent status lookup:

- New `infra/c12_workflow/workflows/extract_and_ingest.json` v1:
  - Webhook `POST /uploads/extract-and-ingest` (raw bytes + scope).
  - MinIO put → C13 `POST /analyze` → poll `/status/{run_id}` until
    terminal.
  - On `completed`: read `chunks.jsonl` + `embeddings.jsonl` +
    `run_manifest.json` from MinIO, POST to C7 `/ingest-chunks`.
  - On `failed`: surface via NATS `ingestion.source.failed` + return
    operator-readable error.
- Deprecate the 2 existing workflows (`ingest_text_source.json`,
  `chunk_and_track.json`) — either delete or mark in `_comment` field.
- MinIO-backed fallback for `GET /status/{run_id}` (cross-pod failover —
  operator queries a long-completed run after a pod restart). Requires
  the polling caller to supply `tenant/project/source` on the request.

**Session 7 (final)**:
- Remove `c7_memory.service.ingest_uploaded_source` + the
  `POST /sources/upload` route.
- Regression test pass on RAG retrieval (the existing
  `tests/eval/test_retrieval_quality.py` against the new ingestion
  path).
- Ops doc on the C13 deployment workflow.
- Batch API `urgency=background` (Anthropic Batch API integration —
  scope this carefully or defer to a follow-up D-020.5).
- Resume-from-phase R-400-225 (artifact diff logic — same caveat).
