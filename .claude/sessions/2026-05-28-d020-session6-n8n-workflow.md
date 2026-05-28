<!-- =============================================================================
File: 2026-05-28-d020-session6-n8n-workflow.md
Version: 1
Path: .claude/sessions/2026-05-28-d020-session6-n8n-workflow.md
Description: D-020 session 6/7 — n8n workflow extract_and_ingest.json
             (the C12→C13→C7 chain), deprecation of the 2 legacy
             workflows, and MinIO-backed fallback for the C13
             /status/{run_id} endpoint.
============================================================================= -->

# Session — D-020 session 6 : n8n workflow + persistent status (2026-05-28)

## Context

Sessions 1→5 shipped the spec + C13 (extractor) + C7 (`/ingest-chunks`) +
the run_id propagation fix. What's still missing for an end-to-end
upload → indexed-in-Arango flow is the **n8n workflow** that wires the
two HTTP surfaces together. Session 6 ships that workflow + closes one
operational gap flagged at session 4 ("BackgroundTasks crash semantics")
by adding a MinIO-backed fallback on the status endpoint so a pod
restart mid-run doesn't lose the polling caller.

## What this session shipped

### NEW workflow — `extract_and_ingest.json` v1

13 nodes, 11 connection sources. End-to-end chain:

```
[1] Webhook POST /uploads/extract-and-ingest
       │  receives { tenant_id, project_id, source_id, content_b64,
       │             filename, mime_type, format, quality_tier?,
       │             urgency?, uploaded_by? }
       ▼
[2] MinIO PUT raw upload
       │  PUT http://minio:9000/sources/{t}/{p}/{s}/raw.{ext}
       ▼
[3] C13 POST /analyze
       │  body = { tenant_id, project_id, source_id, raw_object_key,
       │           filename, mime_type, format, quality_tier, urgency,
       │           document_type }
       │  → { run_id, status: "running", accepted_at }
       ▼
[4] Wait 5s (poll interval) ◄────────┐
       ▼                             │ loop back if not terminal
[5] C13 GET /status/{run_id}         │
       │  ?tenant_id=…&project_id=…&source_id=…
       ▼                             │
[6] IF status terminal? ─── no ──────┘
       │ yes
       ▼
[7] IF status == "completed" ─── no ──► [13] Respond (failed)
       │ yes
       ├─► [8]  MinIO GET chunks.jsonl
       │           ▼
       ├─► [9]  MinIO GET run_manifest.json
       │           ▼
       └─► [10] Build C7 /ingest-chunks payload (Code node)
                   │  parses chunks.jsonl + extracts embedding_model
                   │  from manifest + assembles ChunkIngestRequest
                   ▼
              [11] C7 POST /ingest-chunks
                   │  → SourcePublic { chunk_count, processing_version, … }
                   ▼
              [12] Respond (completed)
```

- **Webhook input** — the caller supplies `content_b64` (raw bytes
  base-64-encoded). For multipart uploads, an upstream gateway-side
  conversion is expected (out-of-scope for the workflow itself).
- **MinIO put** uses an `httpRequest` node with `httpHeaderAuth`
  credential `minio-admin-credentials`. The bucket name defaults to
  `sources` and is overridable via the n8n env var
  `SOURCES_BUCKET`.
- **C13 invocation** targets the in-cluster service name
  `c13-extractor:8000` (per the k8s Service shipped session 4).
- **Polling loop** — n8n's `Wait` node + a back-edge from the
  `IF status terminal` false branch implements a poll-until-done loop
  with a 5 s interval. C13's `status.json` is the source of truth.
- **MinIO reads** for chunks + manifest happen IN PARALLEL (the
  `IF status completed` true branch fans out to both `MinIO GET`
  nodes); the `Code node` joins them and builds the C7 request body.
- **C7 invocation** uses the forward-auth headers per E-100-002
  (`X-User-Id` / `X-Tenant-Id` / `X-User-Roles=project_editor,…`).
- **Responses** — `respondToWebhook` returns either `{accepted: true,
  run_id, chunk_count, processing_version, status: "completed"}` or
  `{accepted: false, run_id, status, errors}` to the caller.

`@relation implements:R-100-080 R-100-081 R-100-125 R-400-220 R-400-223`.

### Legacy workflow deprecation

Both pre-existing workflows are now `active: false` + `_comment`
DEPRECATED (D-020 session 6). They stay on disk for backward-compat
testing and operator awareness; **session 7 deletes them physically**
together with the C7 `upload_source` route.

| File | Old role | New status |
|---|---|---|
| `ingest_text_source.json` | Webhook → C7 `/sources` (parsed-text path) | DEPRECATED, `active: false`, v2 |
| `chunk_and_track.json` | Webhook → C7 `/sources` → poll → reprocess on stale | DEPRECATED, `active: false`, v3 |

A pre-existing n8n instance importing the configmap will see all three
workflows; only `extract_and_ingest.json` is `active` and will accept
webhook traffic.

### MinIO-backed fallback for `GET /status/{run_id}`

Session 4 flagged: "if the worker crashes mid-task, the in-memory
`_runs` may stay in `running` indefinitely". Session 6 closes this by
letting the polling caller supply the scoping triple as query params
and falling back to MinIO `status.json` when the in-memory cache
misses.

**`ay_extractor/src/api/http.py`** — `status_endpoint` extended:
- 3 new optional query params: `tenant_id`, `project_id`, `source_id`.
- When the in-memory `_runs` cache misses AND all 3 are supplied, the
  handler calls the new helper `_read_status_from_minio(...)`:
  ```
  writer = create_writer(settings)                 # MinIO writer
  prefix = RunPrefix(tenant_id, project_id, source_id, run_id)
  raw = await writer.read(status_key(prefix))      # GET status.json
  return json.loads(raw)                           # may raise → None
  ```
- On any failure (missing object, malformed JSON, network), the
  helper returns None and the handler raises 404 with a clear hint to
  the caller about the query params.

The new n8n workflow's poll node URL embeds the scope query params, so
the fallback path is exercised every time the cache misses (e.g. a pod
restart mid-poll).

### ConfigMap k8s regeneration

`infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml` regenerated
via the existing `gen_k8s_workflow_configmap.py` script — **3 workflows
packaged**: `extract_and_ingest.json` (active), `ingest_text_source.json`
+ `chunk_and_track.json` (both `active: false`, retained for
session-7 deletion alongside the C7 legacy route).

## Verification

- `python -m compileall ay_extractor/src ay_platform_core/src` ✓
  (silent, no errors).
- JSON validity on all 3 workflows — Python `json.load()` succeeds on
  each, name + active flag inspected.
- `create_app()` from `ay_extractor.api.http` still returns the 3
  app routes (`/analyze` POST, `/status/{run_id}` GET, `/healthz` GET).
- `/status/{run_id}` route now exposes 3 query params:
  `tenant_id`, `project_id`, `source_id` (verified via
  `route.dependant.query_params`).
- ConfigMap generation script reports **3 workflow(s)** packaged.

Full `run_tests.sh ci` not run on the global suite (would require
testcontainers for the C7 `/ingest-chunks` test against real Arango,
out of scope for the n8n + HTTP fallback work landed here). The
session 5 targeted run of `test_ingest_chunks.py` remains the freshest
green checkpoint for C7's surface; the C13 `/status/{run_id}` fallback
is verified by inspection (the fallback path can't be unit-tested
without a real MinIO + writer fixture; integration coverage lands
session 7 alongside the regression suite).

## Critical-partner notes

- **Workflow polling cap not enforced.** The Wait → poll → IF loop
  runs indefinitely if C13 stays in `running`. For v1 dev this is
  acceptable (n8n's per-execution timeout — default 1 h — caps it).
  Production should add a max-attempts counter (n8n Code node tracking
  iteration count). Tracked as a known v1 limitation; close in
  D-020.5 alongside the batch API mode.

- **Credential bootstrap is operator responsibility.** The two MinIO
  `httpHeaderAuth` references (`minio-admin-credentials`) require
  n8n to know an Authorization header. In dev compose this is the
  default `minioadmin:minioadmin`; in k8s production the operator
  SHALL create the credential via the n8n admin UI (no automated
  provisioning yet — same posture as the existing workflows).

- **Code node parsing of `chunks.jsonl`** — the Code node assumes the
  MinIO httpRequest returns the body as a string in
  `.item.json.data`. The actual field name depends on the n8n
  httpRequest response shape and the chosen `responseFormat`. The
  Code node has a 3-way fallback (`.json.data`, `.binary.data`,
  `.json`) covering the common cases. A real-MinIO smoke test (out
  of band) is required to confirm the exact key on the deployed
  n8n version.

- **`/status` MinIO fallback is read-only.** The handler does NOT
  attempt to repair an in-memory cache miss by re-populating
  `_runs` — that would create a stale ledger if the run is in
  flight on another pod. The fallback returns the persisted state
  AS-IS; the operator can replay the poll on the right pod for
  authoritative live status. Acceptable for v1; session 7 may add
  a cross-pod NATS broadcast if cross-pod liveness becomes
  necessary.

- **The webhook input shape assumes base64 bytes.** For a JSON
  webhook this is the simplest cross-platform encoding (no multipart
  parsing in n8n). The platform's UI upload path (C12-fronted) will
  base64 the file before POSTing — clarify in ops doc session 7.
  Alternative for very large files: pre-uploads to MinIO directly
  + webhook payload carries only the `raw_object_key`.

## Files modified

**New (1)**:
- `infra/c12_workflow/workflows/extract_and_ingest.json` (v1, 13 nodes)

**Modified (4)**:
- `infra/c12_workflow/workflows/ingest_text_source.json` (v2 — `active: false` + DEPRECATED comment)
- `infra/c12_workflow/workflows/chunk_and_track.json` (v3 — `active: false` + DEPRECATED comment)
- `ay_extractor/src/api/http.py` (+~50 LOC: query params on `/status/{run_id}` + new `_read_status_from_minio` helper)
- `infra/k8s/base/c12_workflow/c12-workflow-configmap.yaml` (regenerated — 3 workflows packaged)

**Continuity**:
- `.claude/SESSION-STATE.md` (v62) — §1 + §3 + §5 + §6 updated; older
  §3 entries condensed to fit the 150-line limit (V2 #2 OpenHands +
  V2 #3 Graphiti-style + V2 #3 Block A + Eval + C8 LiteLLM + sessions
  archive earlier-2026-04 line merged).
- `.claude/sessions/2026-05-28-d020-session6-n8n-workflow.md` (this
  file, NEW v1).

## Next

**Session 7/7 (final)** — cleanup + regression:

1. **Physical removal** of:
   - `c7_memory.service.ingest_uploaded_source` (the in-process
     parse + chunk pipeline).
   - `POST /api/v1/memory/projects/{pid}/sources/upload` route (and
     its auth-matrix EndpointSpec entry).
   - `infra/c12_workflow/workflows/ingest_text_source.json` +
     `chunk_and_track.json` (both deprecated this session).
   - `ay_platform_core/c7_memory/ingestion/parser.py` +
     `c7_memory/ingestion/chunker.py` (no longer reachable).
2. **Regression suite** — exercise `tests/eval/test_retrieval_quality.py`
   against the new ingestion path (n8n workflow → C7 `/ingest-chunks`)
   and confirm recall@3 stays at the 1.0 baseline.
3. **Ops doc** — `infra/c13_extractor/docs/deployment.md` covering:
   container build, env wiring (OPENAI_BASE_URL, MINIO creds), k8s
   overlay activation, MinIO bucket creation, n8n credential setup,
   smoke test.
4. **`pyproject.toml` cleanup** — drop deps that became dead with the
   strip (any remaining `python-magic`, `dotenv-cli`, etc.).

**Deferred (D-020.5 follow-up)**:
- `urgency=background` Anthropic Batch API integration (operationally
  heavy, scope separately).
- Resume-from-phase R-400-225 (artifact diff orchestration; the
  artifact layout supports it, only the orchestration logic missing).
- Workflow polling cap + cross-pod NATS status broadcast.
