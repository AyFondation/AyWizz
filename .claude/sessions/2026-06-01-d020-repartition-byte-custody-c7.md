<!-- =============================================================================
File: 2026-06-01-d020-repartition-byte-custody-c7.md
Version: 1
Path: .claude/sessions/2026-06-01-d020-repartition-byte-custody-c7.md
Description: D-020 ingestion re-partition (R-100-081 v3) — byte custody moved
             from C12 (n8n) to C7. The upload-500 debugging exposed that n8n
             is a poor file-byte custodian; the fix re-homes the raw-blob
             write + the workflow trigger + the artifact read in C7, leaving
             C12 a pure metadata orchestrator. Validated end-to-end on the
             live stack.
============================================================================= -->

# Session — D-020 re-partition: byte custody → C7 (R-100-081 v3, 2026-06-01)

## Context

Continuation of the upload-405 work (`2026-05-29-upload-405-...`). After the
405 was fixed (UI → ingestion webhook) the live stack surfaced a **500
"Error in workflow"**, then — once that was peeled — a chain of n8n defects:
the `httpRequest` MinIO node cannot S3-SigV4-sign a write (forcing a base64
transport + an anonymous-bucket workaround), and the workflow read the POST
body as `$json.X` when n8n nests it under `.body` (C13 got empty fields →
422). Each was a workaround on a workaround. The operator asked the load-
bearing question: **"why must the base64 decode live in n8n — can't an infra
component carry it?"**

That reframed the problem. The fragility all stemmed from D-020 v2 assigning
**file-byte custody to C12 (n8n)** — an orchestrator, not a byte mover. The
operator chose **option C** (a component owns the write) + **C7 as the host**
(it already owns the sources surface, project RBAC, and an authenticated
MinIO client), and directed: do all the steps to the end, spec first.

## What this session shipped

### Spec (R-100-081 v3 + R-400-223 v3)

- **R-100-081 v3** (`100-SPEC-ARCHITECTURE.md`): byte custody moves C12 → C7.
  **C7** owns upload reception (`POST …/sources/upload`, multipart), the
  authenticated raw-blob MinIO write, and the metadata-only C12 trigger.
  **C12** becomes a pure metadata orchestrator (never touches a byte, never
  reads/writes MinIO). **C13** unchanged. Explicitly does NOT reinstate the
  synchronous parse+chunk D-020 removed from C7.
- **R-400-223 v3** (`400-SPEC-MEMORY-RAG.md` + §3 prose): `/ingest-chunks`
  receives a **run reference only**; C7 reads `chunks.jsonl` +
  `run_manifest.json` from MinIO itself (was: chunks marshalled in-body by
  n8n). Removes the last place C12 touched object bytes.
- `requirements/CHANGELOG.md` entry.

### C7 (byte custody)

- New `c7_memory/c12_client.py` — `C12WebhookClient` (via
  `observability.make_traced_client`), POSTs metadata to the C12 webhook.
- `config.py` — `c12_webhook_url` (`http://c12:5678/uploads/extract-and-ingest`)
  + `c12_webhook_timeout_s` + `c13_artifacts_bucket` (`c13-extractor-artifacts`).
- `storage/minio_storage.py` — `put_to_bucket` / `get_extraction_artifact` /
  `c13_artifact_key` (explicit bucket + full key, for the C13 artifacts bucket).
- `models.py` — `ChunkIngestRequest` v3 (run-ref only; chunks/embedding fields
  removed, `extra="forbid"`).
- `service.py` — new `store_raw_upload_and_trigger` (writes raw to the C13
  input bucket at `sources/{t}/{p}/{s}/raw.{fmt}` + a copy to C7's bucket for
  download + a `pending` row + triggers C12; 502 + `failed` row on webhook
  error) and `ingest_chunks_from_extractor` v3 (reads manifest + chunks.jsonl
  from MinIO via `_load_c13_artifacts`). KEY FINDING: C13 reads
  `raw_object_key` from its OWN bucket (`OUTPUT_MINIO_BUCKET`), not `sources`
  (`ay_extractor/src/api/http.py` `_read_raw_object`) — the old workflow's
  PUT-to-`sources` was another never-tested bug.
- `router.py` — `POST /sources/upload` multipart (File/Form, `project_editor`+,
  202). `main.py` — wires the C12 client + `aclose`.

### Tests, catalog, env

- `tests/unit/c7_memory/test_ingest_chunks.py` rewritten for v3 (fake storage
  seeds the artifacts) + new `test_upload_source.py`. Integration conftest
  `_ingest_text_via_chunks` rewired to seed MinIO + run-ref. 108 c7
  unit+contract green.
- `tests/e2e/auth_matrix/_catalog.py` — `POST /sources/upload` EndpointSpec.
  Regenerated `requirements/065-TEST-MATRIX.md` + the UI contract snapshot
  `ay_platform_ui/tests/contract/backend-routes.json` (102 routes). Added the
  3 new C7 settings to `.env.example` + `.env.test`. Coherence 25/25.

### UI + workflow + infra

- `ay_platform_ui/lib/apiClient.ts` v9 — `uploadSource` → multipart `FormData`
  via `request()` to `/sources/upload`; dropped `_fileToBase64` + `decodeJWT`.
  apiClient.test + sources.test + api-surface (allowlist now empty) green; UI
  coverage gate passes all 4 thresholds.
- `infra/c12_workflow/workflows/extract_and_ingest.json` v3 — 7 nodes,
  `responseMode: onReceived` (C7's trigger returns instantly; the pipeline
  runs async), webhook(metadata) → C13 /analyze → poll → C7 /ingest-chunks
  (run-ref). No MinIO/decode/respond nodes.
- `tests/docker-compose.yml` — `minio_init` reverted (no anonymous policy;
  `sources` bucket dropped; keeps `c13-extractor-artifacts`); `c13-extractor`
  profiles `[test, litellm]` so it runs in both the mock and dev stacks.
- `ay_platform_ui/tests/system/source-upload.spec.ts` v3 — multipart C7
  upload, asserts 202 + a pending SourcePublic.

## Live validation

`e2e_stack.sh up` (rebuilt the api image with the new C7) + `docker restart
ay-c12-workflow` (registers the v3 webhook), then the Playwright spec:
**1 passed** — UI multipart → C7 `/sources/upload` → C7 stores the raw +
triggers C12 (onReceived) → **202 + pending SourcePublic**. No 404/405/5xx.
The full async completion (chunks actually ingested) needs a real LLM
(litellm/dev); the mock stack proves the wiring.

## Follow-ups

- Run the FULL backend `run_tests.sh ci` before push (this session verified
  ruff+mypy+pytest piecemeal on the changed files + coherence, not the whole
  suite).
- Exercise the real-extraction path on the dev stack (c13 now in the `litellm`
  profile) with a real PDF + valid `.env.secret` keys → assert `completed`.
- The 4 c7 integration tests are testcontainer-gated (not run this session);
  the rewired conftest helper is correct-by-construction but unverified live.

## References

- Prior: `sessions/2026-05-29-upload-405-contract-test-and-c13-stack.md`.
- CLAUDE.md §8.1 (spec-first), §8.4 (contract registry), §13 (auth-matrix), §4.6.
