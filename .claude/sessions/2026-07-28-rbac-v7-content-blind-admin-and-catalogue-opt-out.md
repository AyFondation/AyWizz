<!-- =============================================================================
File: 2026-07-28-rbac-v7-content-blind-admin-and-catalogue-opt-out.md
Version: 1
Path: .claude/sessions/2026-07-28-rbac-v7-content-blind-admin-and-catalogue-opt-out.md
Description: Two operator-driven changes. (1) RBAC E-100-002 v7: drop the
             short-lived tenant_manager role and make admin (= tenant_admin)
             the CONTENT-BLIND, tenant-scoped mirror of platform_manager
             (governance only, no project content); this surfaced and closed a
             cross-tenant user-CRUD isolation hole in C2. (2) LLM tenant
             catalogue becomes OPT-OUT (lazy-materialised from the platform
             registry) with a discovery picker, so a tenant_admin's catalogue
             is never an empty dead-end. Both CIs green; K8s rebuilt+redeployed.
============================================================================= -->

# Session — RBAC v7 (content-blind admin) + LLM catalogue opt-out (2026-07-28)

## Context
Continuation after the `tenant_manager`→`platform_manager` rename. The operator
asked to simplify the tenant tier: keep only `tenant-admin`, with **no access to
project content**, but the **same management capabilities as the super-root,
restricted to its own tenant**. Confirmed via AskUserQuestion: content-blind
total (content reached ONLY through `project_owner/editor/viewer`), and proceed
now despite a large uncommitted diff.

## Part 1 — RBAC E-100-002 v7 (content-blind tenant operator)

**Model.** `platform_manager` (platform-wide, content-blind) · `admin`
(= `tenant_admin`, tenant-scoped, **content-blind**, mirror of platform_manager
within its tenant) · `project_owner/editor/viewer` (the content roles) ·
`user` (baseline). **`tenant_manager` is REMOVED** (it had been added in v6).

- **C2 enum**: dropped `TENANT_MANAGER`; docstring redefines `admin` as the
  content-blind tenant operator.
- **Governance (`admin_router` v3)**: `_require_operator` accepts
  `{platform_manager, admin, tenant_admin}`; `_operator_tenant_scope` = None for
  platform_manager else the caller's tenant. Project governance
  (list/status/ACL) already scoped via `_require_project_operator`. **User
  oversight** (`/admin/users` list + `{id}/deactivate|reactivate`) moved from
  platform_manager-only to `_require_operator` + a new `_require_user_operator`
  (admin confined to its own tenant; 404 absent, 403 cross-tenant).
- **Content-blindness (C4-C7)**: each content router strips `admin`/
  `tenant_admin` from the caller's roles before any content gate
  (`_CONTENT_BLIND_GLOBAL_ROLES`). `admin`-only content ops (run resume, KG
  embed, memory refresh) moved to `project_owner`. C4 source_router editor gate
  drops `admin`. The C4 artifacts SEED endpoint (`/api/v1/admin/...`) stays
  admin-gated by design.
- **Forward-auth** (`router.py`): reverted the v6 tenant_manager viewer
  injection; `/verify` emits the plain forward-auth roles again.

### Cross-tenant isolation hole (found mid-refactor, fixed)
Making `admin` a first-class multi-tenant operator exposed that the C2 per-user
CRUD (`/auth/users` create + `/auth/users/{id}` get/update/delete/reset-password)
was gated by role ONLY, with **no tenant scoping** — an admin of tenant-A could
provision/read/modify/delete/reset users of tenant-B (account takeover +
privilege escalation across tenants). Fixed (`router.py` v6): create FORCES the
caller's `tenant_id`; the `{user_id}` routes gate on `_require_same_tenant_user`
(403 on a cross-tenant target). Regression test added; a stale fixture that only
"passed" because isolation was unenforced (admin in `t-root`, targets in `t-1`)
was realigned to same-tenant (§10.3 case B).

### UI
`admin` gets its tenant-scoped operator console — navbar Projects + Users links
(`isOperator = platform_manager || admin || tenant_admin`), the operator
projects/users pages accept the three operator roles (backend scopes results),
and the classic `/projects` workspace is hidden for operators (content-blind).
Platform-only surfaces (Tenants, LLM registry/providers, global Quotas) stay
`platform_manager`. Negative UI/matrix tests reseeded from `admin`→`user`.

**Spec.** `100-SPEC` v20, E-100-002 v7 (6→5 roles). Auth-matrix content
endpoints drop `admin` from `accept_global_roles`; scoped-admin acceptance is
covered by `test_admin_governance.py` (same convention as project governance).

## Part 2 — LLM tenant catalogue = OPT-OUT + discovery picker (800 v10)

**Problem.** A freshly-seeded tenant's catalogue was EMPTY (curated opt-in), and
a `tenant_admin` had no way to populate it: the "add by id" input was blind and
the platform registry list is `platform_manager`-only. Worse, an empty catalogue
means `model_quality` resolution has nothing to resolve → the tenant is blocked.

**Decision (operator: "both").** Opt-out auto-populate AND a re-add picker.

- **Lazy materialisation** (`catalog_service` v3): on a tenant's first touch
  (list/resolve/project-models/upsert) the catalogue is materialised with every
  registry model (enabled), then a per-tenant `initialized` marker
  (`tenant_llm_catalog_meta`, `catalog_repository` v2) is set so a subsequent
  "remove all" is NOT re-populated. Chosen over a C2→C8 tenant-created event
  (no such wiring exists) — matches the spec's existing LAZY pattern. Trade-off:
  a GET writes on first access.
- **Discovery** (`catalog_router` v2): `GET /api/v1/llm/catalog/available`
  (admin/tenant_admin, tenant-scoped) lists registry models NOT in the tenant
  catalogue — the re-add set — as a public projection (no provider key).
- **UI** (`llm-catalogue` v2): the blind id input becomes a picker fed by
  `/available`.
- **Isolation semantics shift**: both tenants inherit the same registry
  baseline; a tenant's CUSTOMISATION (disable/remove/rate-card) does not leak.
  The two service + e2e "other tenant is empty" isolation tests were rewritten
  to assert customisation isolation (§10.3 case D). Resolution-focused unit
  tests pin `initialized=(_TENANT,)` to keep controlled catalogues; dedicated
  tests cover the lazy path.

**Spec/docs.** `800-SPEC` v10; `065-TEST-MATRIX` 144 endpoints (+available);
`backend-routes.json` regenerated; UI api-surface + contract snapshot updated.

## Verification & deploy
- Backend `run_tests.sh ci`: All stages OK (ruff+mypy+pytest, 2141).
- UI `npm run ci`: green (424 tests, 4 coverage thresholds).
- Images rebuilt (api+ui), K8s dev redeployed (`run.sh dev --restart --wait`).
  RBAC change also triggered a C2 re-seed (role taxonomy changed); the catalogue
  change needs none (lazy per-tenant). All pods Running.

## Process notes
- Recurring §5.7 slip: prefixing `cd <repo>;` makes a composed command the VS
  Code matcher rejects. Dropped the prefix — the session cwd is already the repo
  root; wrappers run directly (`ay_platform_core/scripts/run_tests.sh ci`).
- Both parts flagged their architectural decisions before proceeding (content-
  blind confirmation via AskUserQuestion; catalogue opt-out semantic change).
