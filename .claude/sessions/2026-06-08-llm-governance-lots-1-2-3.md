<!-- =============================================================================
File: 2026-06-08-llm-governance-lots-1-2-3.md
Version: 1
Path: .claude/sessions/2026-06-08-llm-governance-lots-1-2-3.md
Description: Built the multi-tenant LLM-governance operator console in three
             operator-confirmed lots + a self-service quota view. Lot 1: full
             platform LLM registry CRUD + api_base injection + sortable table.
             Lot 2: tenant deactivation + cross-tenant user oversight →
             E-100-002 v2→v3 (tenant_manager = "platform operator"). Lot 3:
             global LLM quota policy (rolling windows, cost+token limits,
             soft→hard enforcement at the C8 gateway) + a navbar quota pill
             visible to every user. 800-SPEC v5, 100-SPEC v16 / E-100-002 v3.
============================================================================= -->

# Session — LLM-governance operator console: Lots 1·2·3 + self quota view (2026-06-08)

## Context
Continuation of the LLM-governance feature whose registry/catalog/model_quality
bricks + HMI #6 were already live (prior sessions). The operator asked for a
3-lot expansion of the platform-admin (tenant_manager / super-root) console,
"les 3 lots dans l'ordre, un par un", then added a cross-cutting request: every
user must see their own quota in real time, from anywhere in the app.

## Lot 1 — LLM registry complete + super-root home fix

- **api_base support** (operator: "Oui — api_base + allowlist regex") added to
  the registry model (`_RegistryFields.api_base`), persisted (`upsert_model` +
  `to_public` + seed mapping), and INJECTED per-call: `UpstreamConnection`
  (NamedTuple `api_key`+`api_base`) returned by `RegistryKeyProvider`, the C8
  client injects both. litellm config v5: `configurable_clientside_auth_params:
  ["api_key", {api_base: <regex>}]` — anti-SSRF allowlist (https-any or internal
  http). **Key insight: api_base is NOT a secret** → `build_registry_key_provider`
  now ALWAYS returns a provider (was None without a master key), so api_base
  injection works key-less; only api_key still needs the master key.
- **Registry DELETE** (repo `delete` + service `delete_model` + router
  `DELETE /admin/v1/llm/registry/{alias}`, 204/404, tenant_manager) + auth-matrix
  EndpointSpec.
- **HMI** (`/admin/llm-registry`): add-model form (provider/upstream/api_base/
  quality/cost/capabilities), per-row delete + api_base display, **sortable
  table** (default quality asc low→high, click headers to re-sort/flip).
- **Super-root home fix**: the content-blind tenant_manager got a raw 403 on
  `/projects`; the page now routes a 403+tenant_manager to an admin home with a
  link to the registry (`projects-admin-home`).

## Lot 2 — tenant console + cross-tenant user oversight (E-100-002 v3)

Operator confirmed "Oui — élargir + MAJ spec": `tenant_manager` evolves from a
tenant-lifecycle super-root into a **platform operator**, WITHOUT gaining any
tenant CONTENT access (content-blindness preserved).

- **Backend (C2)**: `TenantPublic.active` flag + `update_tenant`; service
  `set_tenant_active` (deactivate/reactivate); `list_users(tenant_id?)`
  cross-tenant + `set_user_active`. **Login enforcement**: `issue_token` refuses
  a member of a deactivated tenant (a missing tenant doc = active, so the
  platform super-root login never regresses). Five new admin endpoints
  (tenant_manager): `POST /admin/tenants/{id}/(de|re)activate`, `GET /admin/users`
  (`?tenant_id=`), `POST /admin/users/{id}/(de|re)activate`. User create/delete
  stays the tenant's own admin (that is content).
- **HMI** (under **`/operator/*`** — a UI-owned prefix, NOT `/admin/*`, because
  `/admin/tenants` + `/admin/users` ARE backend API paths → a hard collision;
  `/operator/*` is served by the catch-all, no Traefik route needed):
  `/operator/tenants` (create/delete/deactivate/reactivate) + `/operator/users`
  (cross-tenant list, filter by tenant, deactivate/reactivate).
- **Spec**: `100-SPEC-ARCHITECTURE` v16, **E-100-002 v2→v3** (tenant_manager row
  + permission-resolution clause rewritten; content-blind invariant explicit).

## Lot 3 — global LLM quota policy + enforcement

Operator decisions: **soft→hard** enforcement (warn at threshold, 429 block at
100%) + limits in **cost ($) AND tokens** (first reached triggers). Windows are
**rolling** (Anthropic-style), parametrable in duration AND value, **global** to
all tenants (one policy).

- **`c8_llm/quota/`** package: `models` (QuotaWindow/Policy/Status; defaults
  session-5h/week/month with OPEN limits = inert until configured), `repository`
  (single `llm_quota_policy/global` doc + `usage_since` AQL summing cost+tokens
  from the `llm_calls` ledger per tenant per rolling window — no new metering),
  `service.evaluate` (per-window ok/warn/exceeded), `guard` (best-effort warn,
  raise `QuotaExceededError` on exceeded).
- **Enforcement**: `QuotaGuard` wired into the C8 `LLMGatewayClient` beside the
  key provider (`build_quota_guard(db)` in C3/C4/C7 mains); `_enforce_quota`
  runs BEFORE the upstream send → a blocked tenant raises, no spend.
- **c8_admin API** (tenant_manager): `GET/PUT /admin/v1/quota/policy`,
  `GET /admin/v1/quota/status?tenant_id=`.
- **HMI** `/operator/quotas`: edit the global policy (duration + $/token limits +
  warn% per window) + a tenant-status viewer.
- **Spec**: `800-SPEC-LLM-ABSTRACTION` v5 (global quota policy).

## Self-service quota view (cross-cutting operator request)

Every user must see their OWN tenant's quota live, from anywhere.

- **Backend**: `GET /api/v1/quota/me` — ANY authenticated user, tenant taken
  from the `X-Tenant-Id` forward-auth header (never a client parameter → no
  cross-tenant peeking), reuses `QuotaService.evaluate`. Auth-matrix
  `Auth.AUTHENTICATED` / `Scope.NONE`.
- **HMI** (operator chose "navbar pill + popover"): `QuotaIndicator` in the
  navbar (visible on every protected page, all users) polls `/me` every 60s,
  shows the most-constrained window as a colored pill (green/amber/red, or raw
  `$` when no limit), click → popover with a per-window cost/token/% breakdown.
- **Traefik**: new file-provider routes `c8-admin-quota` (`/admin/v1/quota`,
  prio 100 to beat c2-admin `/admin`) + `c8-admin-quota-self` (`/api/v1/quota`,
  prio 50) → c8-admin, forward-auth gated.

## Tests / CI
- Backend `run_tests.sh ci` **All stages OK — 1931 passed** (final). Per-lot
  gates green throughout (1886 → 1905 → 1929 → 1931).
- UI gate `npm run ci` **green — 47 files / 410 tests**, coverage above all four
  thresholds (stmts 81.8 / branch 70.4 / func 83.5 / lines 85.8).
- New tests: registry api_base/delete + injection (`UpstreamConnection`); C2
  admin platform-operator (deactivate-blocks-login, cross-tenant list/deactivate,
  role gates); quota unit (service ok/warn/exceeded + guard) + integration
  (policy round-trip, real exceeded verdict, self-`/me`) + client enforcement;
  UI operator-tenants/users/quotas + quota-indicator + navbar + contract.
- Catalog-driven CI: `065-TEST-MATRIX.md` 119→128 endpoints; coherence route
  catalog extended (c8_quota_router registered); UI `backend-routes.json` snapshot
  regenerated each lot.

## Decisions
- **tenant_manager = platform operator** (E-100-002 v3): registry + tenant
  lifecycle incl. (de)activation + cross-tenant user oversight (status only) +
  global quotas. Content-blind invariant UNCHANGED.
- **api_base is not a secret** → injectable without a master key.
- **Operator UI prefix `/operator/*`** (not `/admin/*`) to avoid collision with
  backend `/admin/...` API paths; catch-all served, no per-page Traefik route.
- **Quota = one global policy, rolling windows, soft→hard, cost+tokens**; usage
  derived from `llm_calls` (no new store); defaults inert.
- **Autonomy**: `.claude/settings.json` v20 allowlists the seed + 065 generator
  wrappers (operator-requested fewer prompts). Composed shell stays §5.7-banned.

## Honest caveats
- Default quota policy is inert (open limits) → no blocking until limits are set.
- The 429 block raises `QuotaExceededError` at the gateway (the call IS stopped);
  the end-user-facing 429 message polish in the conversation UI is a refinement.
- In dev the mock LLM cost may be ~0 → token limits are the testable dimension.
- Pre-existing seed `source upload 422 (format)` errors are the known upload
  contract bug, unrelated to this work.

## Files (high level)
- Backend: `c8_llm/registry/{models,repository,service,router,seed,key_provider}.py`,
  `c8_llm/client.py`, `c8_llm/quota/{__init__,models,repository,service,guard,router}.py`,
  `c8_admin/main.py`, `c2_auth/{models,db/repository,service,admin_router}.py`,
  `c3_conversation/main.py`, `c4_orchestrator/main.py`, `c7_memory/main.py`.
- Infra: `infra/c8_gateway/config/litellm-config.yaml` v5,
  `infra/c1_gateway/dynamic/routers.yml` (+quota routes).
- UI: `lib/{types,apiClient}.ts`, `app/(protected)/admin/llm-registry/page.tsx`,
  `app/(protected)/operator/{tenants,users,quotas}/page.tsx`,
  `app/(protected)/projects/page.tsx`, `components/{navbar,quota-indicator}.tsx`.
- Specs: `requirements/{100,800}-SPEC-*.md`, `065-TEST-MATRIX.md` (regen).
- Tests: backend `tests/{unit,integration}/c8_llm`, `tests/integration/c8_admin`,
  `tests/integration/c2_auth`, `tests/e2e/auth_matrix/_catalog.py`,
  `tests/coherence/test_route_catalog.py`; UI `tests/integration/operator-*`,
  `quota-indicator`, `navbar`, `tests/contract/api-surface`.
