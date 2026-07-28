<!-- =============================================================================
File: 2026-07-28-project-governance-and-platform-manager-rename.md
Version: 1
Path: .claude/sessions/2026-07-28-project-governance-and-platform-manager-rename.md
Description: Built the cross-tenant PROJECT GOVERNANCE surface for the platform
             operator (E-100-002 v4) end-to-end, then renamed the top global
             role tenant_manager -> platform_manager (E-100-002 v5, breaking).
             Also restored a broken CI baseline (tree-sitter 0.25 + fastapi
             0.139 lazy router inclusion) before the feature work. Both CIs
             green throughout; local K8s rebuilt, redeployed, and C2 re-seeded.
============================================================================= -->

# Session — Project governance (E-100-002 v4) + role rename to `platform_manager` (v5) (2026-07-28)

## Context
Continuation of the platform-operator console. The operator asked for a
super-root (platform operator) **project governance** surface: view every
project across all tenants, manage lifecycle (activate/deactivate/archive),
read+edit the ACL (grant/revoke), and see per-project consumption — **without
any content access** (content-blind preserved). Late in the session the
operator flagged that the role name `tenant_manager` is a misnomer for a
platform-wide owner and requested a full rename to `platform_manager`.

## Env baseline restore (pre-req)
A devcontainer/K8s reconfig had pulled dependency patch upgrades that broke
pre-existing tests. Root-caused + fixed:
- **tree-sitter 0.25** property API (`.type`, `.start_byte`, `.root_node`,
  `parse(bytes)`) — `c7_memory/kg/code_extractor.py`.
- **fastapi 0.139 lazy `_IncludedRouter`**: `include_router` no longer flattens
  child routes into `app.routes`; they live under `.original_router.routes`
  with the include-time `prefix` on `include_context.prefix`. New test helper
  `tests/fixtures/routes.py::iter_api_routes` walks the wrapper (shallow-copies
  when an include prefix applies — the routes are shared module singletons, so
  mutating `.path` corrupts the route-catalog coherence test). 7 roster tests
  migrated.

## Project governance (E-100-002 v4)
- **inc1 — C2 backend** (owns projects): `ProjectStatus` enum (active/inactive/
  archived) on `ProjectPublic`; repo `list_all_projects` (cross-tenant),
  `set_project_status`, `list_project_members`, `append_audit`; service
  governance methods (all audited via new `c2_audit` collection); admin_router
  `/admin/projects` list + `/{id}/{activate,deactivate,archive}` + `/members`
  GET + `/members/{uid}` POST/DELETE — all `_require_platform_manager`.
- **inc2 — UI** `operator/projects` page (status + ACL + consumption),
  `apiClient` methods, `Project.status` + `ProjectMember(List)` types, navbar
  "Projects" (operator).
- **inc3 — URL-scoped enforcement** at C2 `/verify` forward-auth: content URIs
  carrying `{project_id}` reject a non-active project (403), method-aware
  (inactive→all, archived→writes). Governance URIs (`/admin/*`, bare project,
  `/members`) exempt via `_content_project_id`.
- **inc3b — record-derived enforcement** (C3 conversations, no `{project_id}`
  in URL): `ConversationService._enforce_project_status` at the `_require_access`
  choke point + create; C3 reads the C2 governance `c2_projects` status via a
  documented read-only cross-collection probe (R-100-012 single shared DB).
- **Consumption**: extended `GET /admin/v1/quota/status` with an optional
  `project_id` (no new route) → per-project cost/tokens from the `project`
  level; wired into the operator page.

## Role rename `tenant_manager` -> `platform_manager` (E-100-002 v5, BREAKING)
The operator confirmed a full rename (the role is platform-wide, not
tenant-scoped). Approved a one-off §5.2 exception to script the rename
(reviewed via `git diff`), and chose a dev re-seed (over an in-place AQL
migration) for the running cluster.
- **Scripted substring rename** `tenant_manager`/`TENANT_MANAGER` across 74
  files (src/tests/UI/specs/`infra/c1_gateway/dynamic/routers.yml`/system-test
  overlay). Safe: no other identifier contains the token (`tenant_admin`,
  `tenant_id` are distinct). Enum member + value both renamed; gate
  `_require_platform_manager`; `_QUOTA_ROLES`; auth-matrix
  `accept/excluded_global_roles`.
- **Env**: field `local_platform_manager_*` -> env `C2_LOCAL_PLATFORM_MANAGER_*`
  (auto-derived); `.env.example` + `tests/.env.test` synced (Tier-1).
- **Generated regenerated**: `065-TEST-MATRIX.md` + `backend-routes.json`.
- **Test file renamed**: `test_local_tenant_manager_bootstrap.py` ->
  `test_local_platform_manager_bootstrap.py` (`git mv`).
- **Spec**: E-100-002 **v4 -> v5** (pure rename, no power change); 100-SPEC
  doc **v17 -> v18**.
- **Deliberately NOT renamed** (history/meta): `.claude/sessions/*`,
  `.claude/SESSION-STATE.md` historical content, `CLAUDE.md` §13. The old
  name is left there as an accurate record of when it applied.

## Deployment (local docker-desktop K8s)
- `infra/k8s/run.sh` v4 adds `--restart` (force rollout when `:latest` tag
  unchanged). New wrapper `infra/scripts/k8s_reseed_c2.sh` v2 wipes
  `c2_users`+`c2_role_assignments` via a one-shot arangosh **Job** (docker-
  desktop's `kubectl exec` CRI transport is unreliable) then restarts c2-auth.
- Rebuilt api+ui, redeployed, re-seeded. Verified live:
  `demo seed: created user 'superroot' (id=demo-superroot, role=platform_manager)`
  + `project-test`/`project-docgen` recreated with ACL grants.

## Verification
- Backend `run_tests.sh ci` = **All stages OK** (ruff+mypy+pytest, coverage
  gate). UI `npm run ci` = **421 tests, 4 coverage thresholds met**. No
  functional test broken by the rename (only the env-bijection coherence test
  needed the `.env` sync).

## Follow-ups / open
- `git diff` review of the 74-file rename is on the operator (the §5.2
  exception moves review post-hoc).
- Tier-2 `infra/k8s/overlays/dev/.env` may still carry an orphan
  `C2_LOCAL_TENANT_MANAGER_*` (operator-owned; harmless — superroot comes from
  the demo-seed, not `local_platform_manager`).
- `060-IMPLEMENTATION-STATUS.md` regenerated earlier this session.
