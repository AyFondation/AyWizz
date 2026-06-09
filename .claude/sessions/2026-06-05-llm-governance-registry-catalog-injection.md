# 2026-06-05 — Multi-tenant LLM governance: registry + tenant catalogue + model_quality resolution + per-call key injection

**Outcome:** the multi-tenant LLM-governance feature designed on 2026-06-04 is now IMPLEMENTED end-to-end (backend), CI green (`run_tests.sh ci` All stages OK — **1867 passed, 87.74 %**), with real cross-component integration tests proving the live user paths. Code-first per operator directive; spec (800-SPEC / 999-SYNTHESIS) deferred to a closing session.

## Gating spike — litellm per-call key override (CLOSED)

Documented (litellm docs, operator-authorised web): **option B is feasible**. The flag is **per-model** `configurable_clientside_auth_params: ["api_key"]` under `litellm_params` (NOT the broad global `allow_client_side_credentials`). A request-body `api_key` then overrides the deployment key (highest in the credential-resolution chain). Listing **only `api_key`** (not `api_base`) keeps upstream pinned to Anthropic → **no SSRF/exfil seam** (this retired the partner-critical objection raised against B). The proxy env key stays the **fallback** → non-breaking.

## What shipped (4 increments, all test-first, all CI-green)

**#2 Platform registry** (`c8_llm/registry/`): collection `llm_registry` (`_key=model_alias`) + repo + `LLMRegistryService` (single encrypt/decrypt site, `SecretCipher`, AAD `llm_registry:<alias>:api_key`) + **write-only key by construction** (the public projection has NO key-carrying field — not a serializer filter) + idempotent seed from `litellm-config.yaml`. New **`c8_admin`** component package (`COMPONENT_MODULE=c8_admin`, forward-auth) hosts the admin surface — 3 endpoints gated to `tenant_manager` (GET list / PUT model / PUT api-key write-only). New axis **`model_quality`** (low/med/high) introduced, deliberately NOT named `quality_tier` (the orthogonal EnrichmentConfig enrichment-DEPTH axis).

**#4 Tenant catalogue** (`registry/catalog_*`): collection `tenant_llm_catalog` (`_key=tenant:alias`, NO key) + repo + `TenantCatalogService` (CRUD ∩ registry + **resolution `(tenant, model_quality[, capabilities]) → ResolvedModel`**) + 3 endpoints `admin`/`tenant_admin` (scope TENANT, **tenant_manager EXCLUDED** — catalogue is tenant content) + optional chargeback rate-card (explicit rates OR markup_pct, mutually exclusive). Resolution rule: quality match + enabled at BOTH layers + capability filter (vision) + **cheapest-wins** (tie-break alias), **no silent cross-quality downgrade** (None → caller surfaces the gap). Plus `GET .../catalog/resolve` (authenticated, scope TENANT) for the picker + ingestion.

**#5 model_quality drives the model**: `EnrichmentConfig.model_quality` (additive) + a C7 `LLMResolverClient` (HTTP → c8_admin, **graceful**: disabled / unreachable / 404 → no injection, C13 keeps defaults) + C7 service wiring — at upload, resolves the text model (3 enrichment agents) + the vision model (image_analyzer) and injects them into the C13 `llm_assignments` carried by the C12 trigger. `image_analyzer_model` kept as an explicit override (deprecated; removal is a coordinated C12/C13/UI contract change, tracked).

**#3 Per-call key injection (option B, operator-chosen exposure model)**: `litellm-config.yaml` v4 (flag ×3, env fallback) + `LLMGatewayClient.key_provider` injects the decrypted registry key as the request-body `api_key` (best-effort: no provider / None / error → proxy env fallback, the call NEVER breaks) + `RegistryKeyProvider` / `build_registry_key_provider(db)` (None without a master key → injection disabled). Wired in **C3/C4/C7** mains (master key in those components — the operator accepted the wider exposure to fully end the hardcode). C13 (vendored, zero-import isolation) receives its key out-of-band → deferred infra.

## End-to-end test coverage (operator asked for real, not faked, coverage)

Only the LLM proxy is mocked (platform convention — no paid provider key in CI); EVERYTHING else is real:
- **#3 e2e** (`integration/c8_llm/test_key_injection_e2e.py`): real Arango → `set_api_key` encrypts at-rest → `RegistryKeyProvider` decrypts → client injects → **mock proxy receives `api_key == plaintext`** (+ no-key → fallback).
- **#5 cross-component** (`integration/c8_admin/test_c7_resolver_e2e.py`): the REAL C7 `LLMResolverClient` → the REAL c8_admin app (registry+catalogue) over Arango — golden path + tenant isolation + 404 no-downgrade.
- **Golden-path upload** (`integration/c7_memory/test_upload_model_quality_e2e.py`): real Arango+MinIO, user upload → C7 service → real resolver → real c8_admin → resolved `llm_assignments` in the C12 trigger. The whole composition a user upload triggers.
- Plus unit (models / service / seed / resolution all branches / provider all degradations / client injection) + the catalog-driven **auth-matrix** on real backends (registry: write-only key, 403/401, ciphertext-at-rest; catalogue: admin-only, tenant_manager excluded, isolation by header). 065-TEST-MATRIX.md + UI route snapshot regenerated (118 endpoints).

## Deployment wiring (operator-requested)

- **Bootable without a master key**: `LLMRegistryService` cipher made OPTIONAL — list/catalogue/resolve served, key WRITES → 503 (`KeyManagementUnavailableError`), no crash-loop. c8_admin imports/boots without `AY_SECRET_MASTER_KEY` (verified).
- **Compose**: base `docker-compose.yml` v10 adds the `c8-admin` service (Arango-only). **k8s**: `infra/k8s/base/c8_gateway/admin-deployment.yaml` + `admin-service.yaml` (ClusterIP `c8-admin:8000`) + kustomization → **k8s validate L1 OK** (54 docs).
- `C7_C8_ADMIN_URL=http://c8-admin:8000` (same name compose + k8s). `AY_SECRET_MASTER_KEY` is a test default in the root conftest (pytest process) + Tier-2 `.env.secret` at runtime (NEVER in `.env.test`/`.env.example` — it is read directly by `SecretCipher.from_env()`, not a Settings field).

## Decisions taken (with operator)

- **D-021 (proposed)**: app-level LLM registry + encrypted keys at rest (`SecretCipher`, local-master-key, no KMS in v1) + per-call injection (option B, `configurable_clientside_auth_params: ["api_key"]`, env fallback). Master key exposure: **C3/C4/C7** hold it; C13 out-of-band.
- Resolution is **HTTP-only** between components (C7→c8_admin), consistent with the platform's component-boundary ethos (C7→C12, C9→C5/C6), rather than a cross-component direct collection read.
- `model_quality` ≠ `quality_tier` (orthogonal axes; naming clash avoided).

## Deferred (flagged, not blocking)

- **Traefik C1 public route** for c8_admin admin/picker paths → comes with the **HMI (#6)** (no public consumer yet; C7→c8_admin is ClusterIP service-to-service).
- **C13 out-of-band master key** delivery (vendored/isolated) for #3 on the ingestion path.
- **Master-key plumbing in compose/k8s** for C3/C4/C7 + c8-admin (operator adds `.env.secret` to their env_file / `aywizz-secrets`; `overlays/dev/.env` sets `C7_C8_ADMIN_URL`).
- **#6 HMI** (3 surfaces: admin registry / tenant catalogue / project picker, write-only keys), **#7 cost rate-card** (compute_cost uses provider_cost + tenant rate), **#8 spec** (800-SPEC + D-021 in 999-SYNTHESIS + @relation markers + 060 status).
- `image_analyzer_model` removal (coordinated contract change).

## Files

New (src): `c8_llm/registry/{__init__,models,repository,service,router,seed,catalog_models,catalog_repository,catalog_service,catalog_router,key_provider}.py`, `c8_admin/{__init__,config,main}.py`, `c7_memory/llm_resolver.py`.
New (tests): unit `c8_llm/test_registry_{models,service,seed}.py`, `c8_llm/test_tenant_catalog.py`, `c8_llm/test_key_provider.py`, `c8_llm/test_client_key_injection.py`, `c7_memory/test_llm_resolver.py`; integration `c8_admin/test_registry_admin_api.py`, `c8_admin/test_c7_resolver_e2e.py`, `c8_llm/test_key_injection_e2e.py`, `c7_memory/test_upload_model_quality_e2e.py`.
New (infra): `k8s/base/c8_gateway/admin-{deployment,service}.yaml`.
Modified (src): `c8_llm/client.py`, `c7_memory/{config,models,service,main}.py`, `c3_conversation/main.py`, `c4_orchestrator/main.py`.
Modified (tests/infra/config): `conftest.py`, `fixtures/contract_registry.py`, `e2e/auth_matrix/{_catalog,_stack,test_backend_state}.py`, `coherence/test_route_catalog.py`, `unit/c7_memory/test_upload_source.py`, `docker-compose.yml`, `k8s/base/c8_gateway/kustomization.yaml`, `infra/c8_gateway/config/litellm-config.yaml`, `.env.example`, `tests/.env.test`, `requirements/065-TEST-MATRIX.md`, `ay_platform_ui/tests/contract/backend-routes.json`.
