<!-- =============================================================================
File: 2026-05-28-d020-session4-http-wrapper.md
Version: 1
Path: .claude/sessions/2026-05-28-d020-session4-http-wrapper.md
Description: D-020 session 4/7 — FastAPI HTTP wrapper + facade.analyze
             v3 wiring R-400-220 v2 MinIO artifact layout + Docker +
             k8s manifests under infra/c13_extractor/.
============================================================================= -->

# Session — D-020 session 4 : HTTP wrapper + MinIO wiring (2026-05-28)

## Context

After sessions 1+2+3 (spec, strip, adapters), AyExtractor had all the
building blocks but no entry point exposing them as the C13 platform
contract. Session 4 ships:
  - The thin HTTP surface that C12 (n8n) calls.
  - The wiring that actually writes the R-400-220 v2 MinIO artifact
    layout when `facade.analyze` runs in C13 mode.
  - The Dockerfile + k8s manifests + compose fragment under
    `infra/c13_extractor/` so the container is buildable / deployable.

Session 4 **does not** implement: the screener agent (R-800-134
referenced by facade but not yet wired), `urgency=background` Batch API
mode, full resume-from-phase (R-400-225 artifact diff). All three
deferred to subsequent sessions with rationale.

## What this session shipped

### New modules (2)

1. **`storage/minio_layout.py` v1** — MinIO key builders matching
   R-400-220 v2. Central `RunPrefix` dataclass holds the
   `tenant/project/source/runs/{run_id}` tuple; `key("part1", …)`
   returns the full relative MinIO key. Per-artifact getters
   (`run_manifest_key`, `chunks_jsonl_key`, `embeddings_jsonl_key`,
   `status_key`, …) build on top. Counterpart of the Path-based
   `storage/layout.py` (kept for the standalone CLI path).

2. **`api/http.py` v1** — FastAPI app with exactly three endpoints:
   - `POST /analyze` → kicks off `facade.analyze` via
     `BackgroundTasks`, returns `{run_id, status: "running"}`
     immediately with HTTP 202.
   - `GET /status/{run_id}` → returns the in-memory `_runs` registry
     entry (in-cluster source of truth = `status.json` in MinIO,
     used as fallback once the persistent lookup path lands in
     session 5).
   - `GET /healthz` → liveness/readiness probe.
   Module-level `app = create_app()` for `uvicorn` discovery. The
   FastAPI/uvicorn import is wrapped in try/except so missing extras
   raise a clear `ImportError("install with ayextractor[http]")`.

### Modified modules

- **`api/models.py` v3** — `Metadata` gains 5 D-020 fields:
  `tenant_id`, `project_id`, `source_id`, `quality_tier` (Literal
  `minimal | standard | high`, default `minimal`), `urgency` (Literal
  `interactive | background`, default `interactive`). All optional at
  the Python API level (so the legacy CLI still works) — the HTTP
  layer enforces presence at the request boundary via
  `AnalyzeRequest` pydantic model. `AnalysisResult` gains 5 MinIO
  artifact keys (`artifact_prefix`, `manifest_key`, `chunks_key`,
  `embeddings_key`, `status_key`) populated only in MinIO mode.

- **`api/facade.py` v3** — `analyze()` now branches on
  `metadata.tenant_id` to enter MinIO mode:
  1. Resolves a `MinioWriter` via `storage.writer_factory.create_writer`.
  2. Writes initial `status.json` `{status: "running", urgency, …}`
     + `00_metadata/input_fingerprint.json` (sha256 + size + format).
  3. Runs the existing `DocumentPipeline.process()` (Phase 1+2,
     unchanged).
  4. Writes `01_extraction/` (enriched_text.md, structure.json,
     references.json) + `02_chunks/chunks.jsonl` (R-400-222 v2 shape)
     + `02_chunks/chunk_index.json` + `02_chunks/embeddings.jsonl`
     (via `EmbeddingsClient.embed_batch()` — D-020 v2 §B1) +
     `02_chunks/dense_summary.md` (only when `quality_tier=high`).
  5. Writes `00_metadata/run_manifest.json` (R-400-221 v2) stamping
     `ayextractor_version` + `monorepo_git_sha` + `embedding_model` +
     `embedding_dimension` + `embedding_total_tokens` + per-phase
     timing + `config` (quality_tier, urgency, agent toggles).
  6. Writes final `status.json` `{status: "completed"|"failed",
     phases_completed, errors, started_at, completed_at}`.
  7. Returns the populated `AnalysisResult` with the artifact keys.
  Failures during the pipeline are caught: in MinIO mode the
  `status.json` records the error and the call returns a result
  with `chunks_count=0` (caller polls `/status/{run_id}` for terminal
  state, no exception escapes). In CLI mode the exception propagates
  for the standalone caller.

- **`pyproject.toml`** — Added two extras:
  - `http = [fastapi>=0.115, uvicorn[standard]>=0.30]` — required by
    `api/http.py`.
  - `references = [refextract>=1.1.5]` — optional for the regex
    fallback (session 3) to be replaced by the lib parser.
  - `all` extra now includes `http` + `references`.

### Infra (new — `infra/c13_extractor/`)

- **`docker/Dockerfile` v1** — Multi-stage Python 3.13-slim build:
  - Builder: `pip install -e /app/ay_extractor[all]` so every Phase
    1+2 capability is available (pdf, docx, refextract, openai,
    boto3, fastapi, uvicorn).
  - Runtime: `MONOREPO_GIT_SHA` ARG → ENV (so `run_manifest.json`
    records the build commit), non-root `app` user, exposes 8000,
    `CMD uvicorn ayextractor.api.http:app`.

- **`docker-compose.c13.yml` v1** — Dev compose service `c13-extractor`
  binding `${PORT_BASE:-56000}+1300:8000`, env wiring MinIO + C8 +
  embedding model, `depends_on: [minio, litellm]`, `/healthz`
  healthcheck.

- **`infra/k8s/base/c13_extractor/`** — kustomize bundle:
  - `deployment.yaml` v1 (replicas=1 — in-memory `_runs` registry is
    per-pod; horizontal scaling = multiple Deployments behind LB),
    env wiring OPENAI_BASE_URL=`http://litellm:4000/v1` +
    OUTPUT_WRITER=minio + OUTPUT_MINIO_ENDPOINT=`http://minio:9000`,
    envFrom `aywizz-secrets`, baseline resources 1-4 CPU / 2-8 GiB.
  - `service.yaml` v1 — ClusterIP, port 8000.
  - `kustomization.yaml` v1 — bundles both, applies the `aywizz`
    namespace + `app.kubernetes.io/{component,part-of}` labels.
  Not yet referenced from `infra/k8s/base/kustomization.yaml` — the
  C13 deployment is opt-in via the dev/prod overlays in session 5.

## Verification

- `python -m compileall ay_extractor/src ay_extractor/tests` ✓.
- Smoke import test (with package-name shim
  `ayextractor → ay_extractor/src/`) — all 6 new/modified modules
  import cleanly:
  ```
  OK ayextractor.api.models
  OK ayextractor.api.facade
  OK ayextractor.api.http
  OK ayextractor.storage.minio_layout
  OK ayextractor.storage.writer_factory
  OK ayextractor.llm.embeddings_client
  OK ayextractor.extraction.reference_extractor
  OK ayextractor.config.settings
  ```
- `create_app()` returns a FastAPI app titled "C13 — Extraction &
  Chunking Service (AyExtractor)" with exactly 3 application routes
  (`/analyze` POST, `/status/{run_id}` GET, `/healthz` GET) plus the
  FastAPI-default `/docs`, `/openapi.json`, `/redoc`.
- Pydantic validation: `AnalyzeRequest(tenant_id="", …)` raises
  `ValidationError` (min_length=1 on the 3 scoping ids).

## Critical-partner notes

- **Run ID mismatch between HTTP and facade.** The HTTP wrapper
  pre-mints a `run_id` so the caller can poll IMMEDIATELY, but
  `facade.analyze` re-mints its own internal `run_id` for the MinIO
  artifacts. They don't match. The in-memory cache stores both
  (`real_run_id` field). Session 5 will rework this to pass the
  HTTP-side `run_id` into `facade.analyze` and back, so the polled
  id equals the artifact id. Flagged as a known v1 limitation in
  the `/status/{run_id}` 404 message.

- **No screener_log.jsonl yet.** R-400-220 v2 mandates this file when
  `quality_tier=high`, but the `decontextualizer_screener` agent
  (R-800-134) is not yet implemented — it lands in a future session
  focused on the Phase 2 LLM agent surface. The file slot is reserved
  in `storage/minio_layout.py::screener_log_key()` but
  `facade.analyze` doesn't write to it yet. Acceptable for v1 because
  no test mandates the file when the screener doesn't run; failure
  mode = absent file rather than empty file.

- **Background task semantics.** FastAPI `BackgroundTasks` runs AFTER
  the response is sent. If the worker crashes mid-task, the in-memory
  `_runs[run_id]` may stay in `running` indefinitely. Mitigated by
  the MinIO `status.json` final write (the operator can query MinIO
  directly), but the in-memory cache lacks a timeout/retry. Improve
  in session 5 alongside the persistent status lookup.

- **Embedding pass is best-effort.** If `EmbeddingsClient.embed_batch`
  raises (network, model unavailable, …), the pipeline catches and
  logs but DOES NOT fail the overall run — chunks.jsonl still gets
  written, just without `embeddings.jsonl`. C7 `/ingest-chunks` v2
  (session 5) has a backward-compat fallback to embed on the
  receiving side when the field is absent.

- **`status.json` is whole-file overwrite.** Each phase boundary
  writes the FULL payload (not a JSON patch). Simpler + idempotent
  but loses the historical sequence of phase transitions. Acceptable
  for v1 since `run_manifest.json` carries per-phase timing too.

## Files modified

New (4):
- `ay_extractor/src/storage/minio_layout.py` (v1, ~110 LOC)
- `ay_extractor/src/api/http.py` (v1, ~240 LOC)
- `infra/c13_extractor/docker/Dockerfile` (v1)
- `infra/c13_extractor/docker-compose.c13.yml` (v1)
- `infra/k8s/base/c13_extractor/deployment.yaml` (v1)
- `infra/k8s/base/c13_extractor/service.yaml` (v1)
- `infra/k8s/base/c13_extractor/kustomization.yaml` (v1)

Modified:
- `ay_extractor/src/api/facade.py` (v3, +~250 LOC MinIO wiring)
- `ay_extractor/src/api/models.py` (v3, +5 Metadata fields + 5 AnalysisResult fields)
- `ay_extractor/pyproject.toml` (extras `http` + `references` added)

Continuity:
- `.claude/SESSION-STATE.md` (v60) — §1 / §3 / §5 / §6 updated;
  some 2026-05-22/23 §3 entries condensed to fit the 150-line limit.
- `.claude/sessions/2026-05-28-d020-session4-http-wrapper.md` (this file, NEW v1).

## Next

**Session 5/7** — C7 ingest-chunks endpoint + n8n workflow + skipped tests rewrite:

- New C7 endpoint `POST /memory/projects/{pid}/sources/{sid}/ingest-chunks`
  (R-400-223 v2 pure-INSERT path).
- Mark `ingest_uploaded_source` deprecated (path stays for backward
  compat until session 7).
- Update `tests/e2e/auth_matrix/_catalog.py` with the new endpoint
  declaration (E-100-002 RBAC enforcement).
- Rewrite the 2 skipped test files:
  - `tests/unit/config/test_unit_settings.py` v4 against Settings v3
    (smaller surface).
  - `tests/integration/pipeline/test_int_pipeline_subsystem.py`
    rewritten as Phase 1+2 integration with MockLLM + MinIO testcontainer.
- Address the run_id mismatch flagged above (pass HTTP-side
  run_id into facade).
- n8n workflow `extract_and_ingest.json` v1 in
  `infra/c12_workflow/workflows/` (session 6 — bundled with session 5
  iff time allows).
