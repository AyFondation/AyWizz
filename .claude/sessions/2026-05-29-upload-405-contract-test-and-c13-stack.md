<!-- =============================================================================
File: 2026-05-29-upload-405-contract-test-and-c13-stack.md
Version: 1
Path: .claude/sessions/2026-05-29-upload-405-contract-test-and-c13-stack.md
Description: Source-upload 405 fix + non-regression guards. UI rewired to the
             D-020 ingestion webhook; a UI↔backend contract-test tier added
             (catches front/back drift the mocked tests can't); C13 wired
             into the test-profile compose stack; a real-login Playwright
             system spec for upload. All verified on the live stack run from
             the devcontainer (wrapper-encapsulated docker compose).
============================================================================= -->

# Session — Upload-405 fix + contract-test tier + C13 in the stack (2026-05-29)

## Context

A user-reported bug: uploading a source file for chunking returned **HTTP
405**. Root cause = front/back **contract drift**. D-020 (sessions 1→7,
2026-05-28) retired C7's in-process multipart upload, but the UI client
(`lib/apiClient.uploadSource`) kept POSTing the removed
`/api/v1/memory/projects/{pid}/sources/upload`. That path then matched
`GET/DELETE /sources/{source_id}` (with `source_id="upload"`) → POST not
allowed → 405. The mocked vitest/MSW tests could not catch it: a mock
encodes the SAME wrong contract as the code, so the upload test was green
while production was broken.

The operator asked for the full fix **plus** non-regression guards
("tests de non régression", "des tests de bout en bout qui testent toutes
les fonctions") — and then to actually run the stack.

## What this session shipped

### 1. The fix (UI)

- `lib/apiClient.uploadSource` (v8) rewired to the D-020 C12 (n8n)
  ingestion webhook **`POST /uploads/extract-and-ingest`** (JSON body,
  file as base64 `content_b64`, `tenant_id` from the JWT claims). The two
  misleading vitest tests corrected to the new contract (§10.4 case D).

### 2. Contract-test tier (the "all-functions" regression guard)

The anti-drift chain `live routers → catalog → snapshot → UI client`:

- `scripts/checks/export_route_catalog_json.py` (v1) — exports the
  auth-matrix catalog (`tests/e2e/auth_matrix/_catalog.py`) to a
  language-neutral JSON snapshot (`--write`/`--check`).
- `ay_platform_ui/tests/contract/backend-routes.json` — the **101-route**
  snapshot consumed by the UI test.
- `tests/coherence/test_ui_contract_snapshot.py` (v1) — pins the snapshot
  to the catalog (build red on drift). The catalog itself is pinned to the
  live FastAPI routers by the pre-existing `test_route_catalog.py`.
- `ay_platform_ui/tests/contract/api-surface.test.ts` (v1) — drives EVERY
  `apiClient` method through a capturing `fetch` spy + a completeness guard
  (all prototype methods exercised) + a method-aware path matcher (handles
  `{param}` and `{path:path}` catch-all). Asserts every (method, path) the
  client emits resolves to a backend route; only `/uploads/extract-and-ingest`
  (n8n, not FastAPI) is allowlisted. **Would have caught the 405** (POST
  resolves to the GET/DELETE `…/sources/{id}` path but no POST → violation).

### 3. C13 in the dev/test compose stack

- `tests/docker-compose.yml` (v9): added the `c13-extractor` service
  (profile `test`, co-located with `mock_llm`, `OPENAI_BASE_URL=
  http://mock_llm:8000/v1`). `minio_init` now creates the `sources` and
  `c13-extractor-artifacts` buckets (+ policy) the workflow PUTs/GETs.
  Operator decision (mock LLM has no `/v1/embeddings`): C13 terminates
  `failed` at the embedding phase — proves the WIRING, not real extraction.
- `scripts/e2e_stack.sh` (v9): `up`/`build` (thus `full`) now pass
  `--profile test` so mock_llm + c13 actually start (CI flows passed no
  profile, so they never ran). `dev` keeps its own `litellm` profile.

### 4. Real-login Playwright system spec

- `ay_platform_ui/tests/system/source-upload.spec.ts` (v1) — API-level
  (`request`): real `/auth/login` (alice/seed-password), decode JWT
  `tenant_id`, POST the webhook exactly like `uploadSource`. PRIMARY
  assertion = **never 404/405** (the regression class); SECONDARY = terminal
  `{status}/{accepted}` envelope if 200. Does NOT assert `completed`
  (mock-LLM path).

## Real-stack verification (devcontainer)

`docker compose` is denied, but `e2e_stack.sh` is an allowlisted wrapper
and a Docker Desktop daemon is reachable. `e2e_stack.sh full` ran:
**build ✅ (all images incl. ay-c13-extractor) + boot ✅ (all containers
healthy)**. Three environment fixes were needed for an in-container run
(all §10.4 case B, justified):

- `e2e_stack.sh` v9 `cmd_seed`/`cmd_system` now use the existing
  `_internal_url` helper (localhost→host.docker.internal in a container;
  no-op on the host). Previously only `cmd_dev` used it → seed failed with
  ConnectError.
- `tests/system/conftest.py` v3 `_mock_llm_admin_url` derives its host from
  `STACK_BASE_URL` instead of hardcoding `localhost:59800` (was 2 errors
  from inside a container).
- `ay_platform_ui/tests/system/_global-setup.ts` v2 downgrades the
  `dev_credentials` check from throw → warn — it gated the WHOLE UI system
  tier to the `dev` stack; real-login specs (source-upload) run against
  `full`. Reachability + `auth_mode=local` stay hard preconditions.

Results: backend `tests/system` = **39 passed, 4 skipped, 1 xfail, 0
errors** (incl. `test_uploads_path_reaches_n8n` and
`test_uploads_anonymous_is_401_via_traefik`); Playwright
`source-upload.spec.ts` = **1 passed** against the live stack (chromium ran
in-container; no 404/405). UI gate stays green (350 tests, 4/4 thresholds);
lint + typecheck + ruff + mypy clean on all touched files.

## Notes / follow-ups (non-blocking)

- **n8n needs `docker restart ay-c12-workflow` once after seed** for the
  imported webhook to register (the process keeps its in-memory router;
  the `test_uploads_to_retrieval` xfail documents this). Candidate
  follow-up: have `c12_workflow_seed` restart c12, or activate via the n8n
  REST API.
- C13 healthcheck stays `health: starting` (the `wget` in the c13 image's
  healthcheck command is likely absent; the app serves `/healthz`).
  Cosmetic.
- The webhook MinIO-PUT node sends `content_b64` as a literal string body
  (not base64-decoded) — verify the raw object is not corrupted when real
  extraction is exercised.
- n8n stored credential `minio-admin-credentials` is not seeded by compose
  — the workflow's MinIO nodes may need it created for a full
  `completed` run.

## References

- D-020 chain: `sessions/2026-05-28-d020-*.md`.
- CLAUDE.md §13 (auth-matrix catalog), §8.4 (contract registry), §10.4
  (test modification discipline), §4.5 (infra conventions).
