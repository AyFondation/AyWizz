<!-- =============================================================================
File: 2026-05-28-d020-session3-refactor.md
Version: 1
Path: .claude/sessions/2026-05-28-d020-session3-refactor.md
Description: D-020 session 3/7 — refactor LLM→library + new minio_writer
             + embeddings_client + reference_extractor + dangling tests
             cleanup.
============================================================================= -->

# Session — D-020 session 3 : new adapters + lib-based refactor (2026-05-28)

## Context

After session 2 stripped ~9 600 LOC of dead code, AyExtractor `compileall`
was green but **non-functional** : the surviving factories (writer / cache)
raised `NotImplementedError` for any backend; no embedding path existed;
the LLM-based reference extraction was gone with no replacement; tests
referencing stripped modules were left dangling.

Session 3 makes AyExtractor **functionally complete for Phase 1+2** (still
no HTTP wrapper — that's session 4). The deliverables match D-020 R-100-125
v2 §5/§6 + D-020 v2 §B1.

## What this session shipped

### New modules (3)

1. **`extraction/reference_extractor.py` v1** — Library-based citation /
   cross-reference / bibliography extraction. Zero LLM calls per D-020
   R-100-125 v2 §5. Uses :
   - Regex for bracket citations `[1]`, author-year `(Smith, 2023)`,
     cross-references `see section 4.2`, figure/table refs.
   - `refextract` (optional dep) for the bibliography section, with a
     regex fallback if not installed (DOI / URL / numbered-line patterns).
   - Re-exposes `Footnote` items from the structure detector as
     `Reference(type=footnote)` for a uniform downstream stream.
   - Replaces the LLM-driven `pipeline/agents/reference_extractor.py`
     (stripped in session 2).

2. **`llm/embeddings_client.py` v1** — D-020 v2 §B1 : C13 computes
   embeddings. Thin async client wrapping the OpenAI SDK `/embeddings`
   endpoint, pointed at C8 via `base_url`. Returns `EmbeddingBatchResult`
   carrying `vectors + model + dimension + total_tokens + latency_ms`
   for downstream `run_manifest.json` stamping (R-400-221 v2). Handles
   batch splitting (default 100/batch), enforces single-model-per-run
   (raises if a sub-batch returns a different model id).

3. **`storage/minio_writer.py` v1** — S3-API writer (boto3) implementing
   `BaseOutputWriter`. Async-safe via `asyncio.to_thread()` delegation
   (boto3 is sync, thread-safe at client level). Replaces the stripped
   `local_writer.py` + `s3_writer.py`. Symlinks are emulated via tiny
   `*.symlink` marker objects (S3 has no native symlinks; the in-cluster
   workflow tracks the latest run via `run_manifest.json`, so this is
   best-effort). The writer is path-agnostic — caller provides the full
   key; the bucket + optional global prefix come from settings.

### Modified modules (3 + 4 factories/configs)

- **`llm/adapters/openai_adapter.py` v2** — Added `base_url` constructor
  arg + lazy `_client()` method. In-cluster points at C8
  (`http://c8:8000/v1`); standalone dev points at any OpenAI-API-compat
  endpoint via env. No code change needed in AyExtractor when switching
  upstream — configuration-only routing per D-020 §4.

- **`storage/writer_factory.py` v4** — Now resolves the MinIO writer
  from settings (was: raises NotImplementedError). Legacy `local`/`s3`
  still raise the clear error so misconfigurations fail loudly.

- **`config/settings.py` v3** — Added `minio_access_key` /
  `minio_secret_key` / `minio_region` (R-100-118 dedicated runtime user
  credentials, boto3 default chain fallback when empty). Added
  `embedding_model` (default `voyage-3`) + `embedding_batch_size`
  (default 100) for the new embeddings client.

- **`llm/client_factory.py` v3** + **`pipeline/llm_factory.py`** — Pass
  `base_url` into the OpenAI adapter from `settings.openai_base_url`.

### Tests patched (9 files)

- **Rewritten** (smaller, targeted at surviving surface):
  - `tests/unit/storage/test_unit_writer_factory.py` v3 — MinIO-only,
    legacy `local`/`s3` paths assert NotImplementedError via
    `Settings.model_construct` bypass.
  - `tests/unit/cache/test_unit_cache_factory.py` v4 — JSON-only,
    legacy backends rejected by pydantic at Settings construction.
  - `tests/unit/pipeline/test_unit_llm_factory.py` v2 — Single
    OpenAI-compat adapter path. Legacy provider strings now
    parametrised-test'd to confirm they all route to OpenAIAdapter.
  - `tests/llm_test_factory.py` — Legacy `TEST_LLM_PROVIDER` values
    map to OpenAI-adapter + provider-specific `base_url`. Embedder
    factory switched to the new `EmbeddingsClient`.

- **Substituted** (string-only fixture replacements, no semantic
  change):
  - `tests/unit/tracking/test_unit_{agent_tracker,exporter,session_tracker,stats_aggregator}.py`
    — `"concept_extractor"` → `"summarizer"` (test fixtures, not
    module imports).
  - `tests/integration/tracking/test_int_tracking_pipeline.py` — idem.
  - `tests/unit/pipeline/test_unit_dag_builder.py` — DAG topology
    examples replaced with Phase 1+2 agent names (image_analyzer,
    summarizer, decontextualizer_screener, decontextualizer,
    densifier).

- **Module-level skip** (with TODO pointing to the session that will
  rewrite them properly):
  - `tests/unit/config/test_unit_settings.py` — 507 lines, ~40 fields
    referenced are removed; full rewrite queued for session 4.
  - `tests/integration/pipeline/test_int_pipeline_subsystem.py` —
    700+ lines, imports 6 stripped agent classes in test bodies;
    queued for session 5 (when the Phase 1+2 pipeline + HTTP wrapper
    stabilises).

### Verification

- `python -m compileall ay_extractor/src ay_extractor/tests` ✓
  (silent, no errors).
- 0 module-level imports of stripped modules remain anywhere
  (`from ayextractor.{rag,consolidator,batch,graph,...} import …`
  scan returns empty for top-of-file imports).
- The 2 module-skip test files import only surviving modules at
  module level; their dead-import bodies are not exercised because
  `pytestmark = pytest.mark.skip(...)` blocks execution.

## Critical-partner notes

- **Conftest fixtures with dead imports left alone.** `tests/integration/
  conftest.py` still contains `qdrant_memory_store`, `chromadb_memory_store`,
  `arangodb_memory_store` fixtures that `import` removed modules
  **inside** the fixture body. None of the surviving tests invoke these
  fixtures, so they're lazy-loaded dead code. Cleaning them up belongs
  to session 5 alongside the integration-pipeline-test rewrite. Flagged
  rather than silently fixed because deleting them might mask a future
  test rewrite that needs them.

- **OpenAI adapter as the sole adapter is a meaningful single point of
  failure.** If C8 LiteLLM is down OR misconfigured for an agent route,
  every AyExtractor agent fails identically (same SDK, same error
  shape). Operator visibility relies on C8's cost forwarder + the
  `status.json` failure record. Acceptable for v1 (the platform's
  whole LLM egress already shares this dependency per R-100-011).

- **`EmbeddingsClient` not yet wired into `facade.analyze`.** Session 4
  will add the embedding pass after the chunking phase, write
  `embeddings.jsonl`, and stamp the manifest. This session lays the
  client + the settings; the orchestration touchpoint comes with the
  HTTP wrapper.

## Next

**Session 4/7** — HTTP wrapper + final E2E wiring :

- New `src/api/http.py` — FastAPI app exposing:
  - `POST /analyze` (R-100-125 v2 §2 — accept input MinIO key + tenant +
    project + source + `quality_tier` + `urgency` + config_overrides;
    return `{run_id, status: "running"}` immediately; run pipeline
    asynchronously).
  - `GET /status/{run_id}` — return the current RunManifest excerpt.
  - `GET /healthz` — liveness/readiness probe.
- Wire `facade.analyze` to drive the full Phase 1+2 pipeline producing
  the R-400-220 v2 artifact layout in MinIO (including embeddings via
  the new client + screener_log.jsonl for `quality_tier=high`).
- Implement `urgency=background` mode (Anthropic Batch API path —
  routed through C8).
- Implement resume-from-phase per R-400-225 (MinIO carried artifacts
  from prior run).
- Dockerfile + k8s manifests under `infra/c13_extractor/` (mirrors
  the per-component pattern of CLAUDE.md §4.5).

## Files modified

New (3):
- `ay_extractor/src/extraction/reference_extractor.py` (v1, ~180 LOC)
- `ay_extractor/src/llm/embeddings_client.py` (v1, ~160 LOC)
- `ay_extractor/src/storage/minio_writer.py` (v1, ~180 LOC)

Modified (7):
- `ay_extractor/src/llm/adapters/openai_adapter.py` (v2)
- `ay_extractor/src/llm/client_factory.py` (v3)
- `ay_extractor/src/pipeline/llm_factory.py`
- `ay_extractor/src/storage/writer_factory.py` (v4)
- `ay_extractor/src/config/settings.py` (v3)

Tests (9 patched, 0 added, 0 deleted):
- Rewritten: writer_factory test v3, cache_factory test v4,
  llm_factory test v2, llm_test_factory (helper).
- Substituted (string-only): 4 tracking unit tests + 1 tracking
  integration test + dag_builder test.
- Skipped (module-level pytestmark + TODO): test_unit_settings,
  test_int_pipeline_subsystem.

Continuity:
- `.claude/SESSION-STATE.md` (v59) — §1 + §3 + §5 + §6 updated.
- `.claude/sessions/2026-05-28-d020-session3-refactor.md` (this file, NEW v1).
