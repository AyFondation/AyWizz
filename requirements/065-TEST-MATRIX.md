---
document: 065-TEST-MATRIX
version: 1
path: requirements/065-TEST-MATRIX.md
language: en
status: approved
derives-from: [E-100-002]
---

# Auth × Role × Scope Test Matrix

> **Auto-generated.** Source of truth: [`tests/e2e/auth_matrix/_catalog.py`](../ay_platform_core/tests/e2e/auth_matrix/_catalog.py). Regenerate via `python ay_platform_core/scripts/checks/generate_test_matrix_doc.py --write requirements/065-TEST-MATRIX.md`.

## 1. Test strategy

Every HTTP route exposed by any platform component is exercised along **five dimensions** (E-100-002 v2 verification clause):

1. **Anonymous access** — no identity headers, no Bearer JWT. Endpoint MUST NOT return a 2xx. (`tests/e2e/auth_matrix/test_anonymous_access.py`)
2. **Role gate** — for every ROLE_GATED endpoint, an authenticated user lacking the required role MUST receive 403; a user holding any of the accepted roles MUST clear the gate. (`tests/e2e/auth_matrix/test_role_matrix.py`)
3. **Cross-tenant isolation** — same role, wrong `X-Tenant-Id`. MUST return 403/404 (no leak). (`tests/e2e/auth_matrix/test_isolation.py`)
4. **Cross-project isolation** — correct tenant, role granted on a DIFFERENT project. MUST return 403/404. (`tests/e2e/auth_matrix/test_isolation.py`)
5. **Backend state** — write/delete endpoints SHALL be observable in ArangoDB / MinIO after a successful call; the matrix asserts directly on the persistence layer. (`tests/e2e/auth_matrix/test_backend_state.py`)

Authentication-mode coverage (`local` / `entraid` / `none`) is tested at the C2 boundary in `tests/e2e/auth_matrix/test_auth_modes.py` — the modes only differ in HOW the JWT is minted; downstream components consume the same forward-auth headers regardless.

## 2. Role hierarchy (E-100-002 v2)

**Global roles**:

- `tenant_manager` — super-root, content-blind. Tenant lifecycle ONLY.
- `admin` — tenant-scoped admin (synonyms in v2).
- `tenant_admin` — tenant-scoped admin (synonyms in v2).
- `user` — baseline authenticated user.

**Project-scoped roles** (per-project, in JWT `project_scopes`):

- `project_owner`
- `project_editor`
- `project_viewer`

## 3. Endpoint catalog

**101 endpoints** across 7 components. Order: by component, method, path.

### c2_auth

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `GET` | `/auth/config` | open | — | *(open)* | — | — | 200 |
| `GET` | `/ux/config` | open | — | *(open)* | — | — | 200 |
| `POST` | `/auth/token` | open | — | *(open)* | — | — | 200 |
| `POST` | `/auth/login` | open | — | *(open)* | — | — | 200 |
| `GET` | `/auth/verify` | authenticated | — | any authenticated | — | — | 200 |
| `POST` | `/auth/logout` | authenticated | — | any authenticated | — | — | 204 |
| `POST` | `/auth/users` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | arango · `c2_users` | 201 |
| `GET` | `/auth/users/{user_id}` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | — | 200 |
| `PATCH` | `/auth/users/{user_id}` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | arango · `c2_users` | 200 |
| `DELETE` | `/auth/users/{user_id}` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | arango · `c2_users` | 204 |
| `POST` | `/auth/users/{user_id}/reset-password` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | — | 204 |
| `GET` | `/auth/sessions` | role_gated | — | `admin` | `tenant_manager` | — | 200 |
| `DELETE` | `/auth/sessions/{session_id}` | role_gated | — | `admin` | `tenant_manager` | — | 204 |
| `POST` | `/admin/tenants` | role_gated | — | `tenant_manager` | — | arango · `c2_tenants` | 201 |
| `GET` | `/admin/tenants` | role_gated | — | `tenant_manager` | — | — | 200 |
| `DELETE` | `/admin/tenants/{tenant_id}` | role_gated | — | `tenant_manager` | — | arango · `c2_tenants` | 204 |
| `POST` | `/api/v1/projects` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | arango · `c2_projects` | 201 |
| `GET` | `/api/v1/projects` | authenticated | tenant | any authenticated | — | — | 200 |
| `GET` | `/api/v1/projects/{project_id}` | authenticated | tenant | any authenticated | `tenant_manager` | — | 200 |
| `PATCH` | `/api/v1/projects/{project_id}` | role_gated | tenant | `admin` · `tenant_admin` · `project_owner` | `tenant_manager` | arango · `c2_projects` | 200 |
| `DELETE` | `/api/v1/projects/{project_id}` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | arango · `c2_projects` | 204 |
| `GET` | `/api/v1/users/me/preferences` | authenticated | tenant | any authenticated | `tenant_manager` | — | 200 |
| `PUT` | `/api/v1/users/me/preferences` | authenticated | tenant | any authenticated | `tenant_manager` | arango · `c2_user_preferences` | 200 |
| `POST` | `/api/v1/projects/{project_id}/members/{user_id}` | role_gated | project | `admin` · `tenant_admin` · `project_owner` | `tenant_manager` | arango · `c2_role_assignments` | 204 |
| `DELETE` | `/api/v1/projects/{project_id}/members/{user_id}` | role_gated | project | `admin` · `tenant_admin` · `project_owner` | `tenant_manager` | arango · `c2_role_assignments` | 204 |
### c3_conversation

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `GET` | `/api/v1/conversations` | authenticated | tenant | any authenticated | — | — | 200 |
| `POST` | `/api/v1/conversations` | authenticated | tenant | any authenticated | — | arango · `c3_conversations` | 201 |
| `GET` | `/api/v1/conversations/{conversation_id}` | authenticated | tenant | any authenticated | — | — | 200 |
| `PATCH` | `/api/v1/conversations/{conversation_id}` | authenticated | tenant | any authenticated | — | arango · `c3_conversations` | 200 |
| `DELETE` | `/api/v1/conversations/{conversation_id}` | authenticated | tenant | any authenticated | — | arango · `c3_conversations` | 204 |
| `GET` | `/api/v1/conversations/{conversation_id}/messages` | authenticated | tenant | any authenticated | — | — | 200 |
| `POST` | `/api/v1/conversations/{conversation_id}/messages` | authenticated | tenant | any authenticated | — | — | 200 |
| `GET` | `/api/v1/conversations/{conversation_id}/events` | authenticated | tenant | any authenticated | — | — | 200 |
### c4_orchestrator

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `POST` | `/api/v1/orchestrator/runs` | authenticated | tenant | any authenticated | — | arango · `c4_runs` | 201 |
| `GET` | `/api/v1/orchestrator/runs/{run_id}` | authenticated | tenant | any authenticated | — | — | 200 |
| `POST` | `/api/v1/orchestrator/runs/{run_id}/feedback` | authenticated | tenant | any authenticated | — | — | 200 |
| `POST` | `/api/v1/orchestrator/runs/{run_id}/resume` | role_gated | tenant | `admin` | `tenant_manager` | — | 200 |
| `GET` | `/api/v1/orchestrator/runs/{run_id}/trace` | authenticated | tenant | any authenticated | — | — | 200 |
| `POST` | `/api/v1/orchestrator/runs/{run_id}/steer` | authenticated | tenant | any authenticated | — | — | 200 |
| `GET` | `/api/v1/projects/{project_id}/artifacts/runs` | authenticated | project | any authenticated | `tenant_manager` | arango · `c4_artifact_runs` | 200 |
| `GET` | `/api/v1/projects/{project_id}/artifacts/runs/{run_id}/tree` | authenticated | project | any authenticated | `tenant_manager` | minio · bucket `orchestrator` | 200 |
| `GET` | `/api/v1/projects/{project_id}/artifacts/runs/{run_id}/blob` | authenticated | project | any authenticated | `tenant_manager` | minio · bucket `orchestrator` | 200 |
| `POST` | `/api/v1/admin/projects/{project_id}/artifacts/seed` | role_gated | tenant | `admin` · `tenant_admin` | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 200 |
| `GET` | `/api/v1/projects/{project_id}/git/commits` | authenticated | project | any authenticated | `tenant_manager` | — | 200 |
| `POST` | `/api/v1/projects/{project_id}/documents` | authenticated | project | any authenticated | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 201 |
| `PUT` | `/api/v1/projects/{project_id}/documents/{path:path}` | authenticated | project | any authenticated | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 200 |
| `GET` | `/api/v1/projects/{project_id}/documents` | authenticated | project | any authenticated | `tenant_manager` | — | 200 |
| `GET` | `/api/v1/projects/{project_id}/documents/{path:path}` | authenticated | project | any authenticated | `tenant_manager` | — | 200 |
| `DELETE` | `/api/v1/projects/{project_id}/documents/{path:path}` | authenticated | project | any authenticated | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 204 |
| `POST` | `/api/v1/projects/{project_id}/documents/mkdir` | authenticated | project | any authenticated | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 201 |
| `POST` | `/api/v1/projects/{project_id}/documents/rename` | authenticated | project | any authenticated | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 200 |
| `POST` | `/api/v1/projects/{project_id}/documents/move` | authenticated | project | any authenticated | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 200 |
| `GET` | `/api/v1/projects/{project_id}/source/tree` | authenticated | project | any authenticated | `tenant_manager` | — | 200 |
| `POST` | `/api/v1/projects/{project_id}/source/mkdir` | role_gated | project | `admin` · `project_owner` · `project_editor` | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 201 |
| `POST` | `/api/v1/projects/{project_id}/source/rename` | role_gated | project | `admin` · `project_owner` · `project_editor` | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 200 |
| `POST` | `/api/v1/projects/{project_id}/source/move` | role_gated | project | `admin` · `project_owner` · `project_editor` | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 200 |
| `GET` | `/api/v1/projects/{project_id}/source/file/{path:path}/meta` | authenticated | project | any authenticated | `tenant_manager` | — | 200 |
| `DELETE` | `/api/v1/projects/{project_id}/source/file/{path:path}` | role_gated | project | `admin` · `project_owner` · `project_editor` | `tenant_manager` | both · `c4_artifact_runs` · bucket `orchestrator` | 204 |
### c5_requirements

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `GET` | `/api/v1/projects/{project_id}/requirements/documents` | authenticated | project | any authenticated | — | arango · `c5_documents` | 200 |
| `POST` | `/api/v1/projects/{project_id}/requirements/documents` | role_gated | project | `admin` · `tenant_admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `c5_documents` | 201 |
| `GET` | `/api/v1/projects/{project_id}/requirements/documents/{slug}` | authenticated | project | any authenticated | — | arango · `c5_documents` | 200 |
| `PUT` | `/api/v1/projects/{project_id}/requirements/documents/{slug}` | role_gated | project | `admin` · `tenant_admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `c5_documents` | 200 |
| `DELETE` | `/api/v1/projects/{project_id}/requirements/documents/{slug}` | role_gated | project | `admin` · `tenant_admin` · `project_owner` | `tenant_manager` | arango · `c5_documents` | 204 |
| `GET` | `/api/v1/projects/{project_id}/requirements/entities` | authenticated | project | any authenticated | — | arango · `c5_entities` | 200 |
| `GET` | `/api/v1/projects/{project_id}/requirements/entities/{entity_id}` | authenticated | project | any authenticated | — | arango · `c5_entities` | 200 |
| `PATCH` | `/api/v1/projects/{project_id}/requirements/entities/{entity_id}` | role_gated | project | `admin` · `tenant_admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `c5_entities` | 200 |
| `DELETE` | `/api/v1/projects/{project_id}/requirements/entities/{entity_id}` | role_gated | project | `admin` · `tenant_admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `c5_entities` | 204 |
| `GET` | `/api/v1/projects/{project_id}/requirements/entities/{entity_id}/history` | authenticated | project | any authenticated | — | — | 200 |
| `GET` | `/api/v1/projects/{project_id}/requirements/entities/{entity_id}/versions/{version}` | authenticated | project | any authenticated | — | — | 501 |
| `GET` | `/api/v1/projects/{project_id}/requirements/relations` | authenticated | project | any authenticated | — | — | 200 |
| `GET` | `/api/v1/projects/{project_id}/requirements/tailorings` | authenticated | project | any authenticated | — | — | 200 |
| `POST` | `/api/v1/projects/{project_id}/requirements/reindex` | role_gated | project | `admin` · `project_owner` | `tenant_manager` | — | 202 |
| `GET` | `/api/v1/projects/{project_id}/requirements/reindex/{job_id}` | authenticated | project | any authenticated | — | — | 200 |
| `POST` | `/api/v1/projects/{project_id}/requirements/reconcile` | role_gated | project | `admin` · `project_owner` | `tenant_manager` | — | 200 |
| `GET` | `/api/v1/projects/{project_id}/requirements/export` | authenticated | project | any authenticated | — | — | 200 |
| `POST` | `/api/v1/projects/{project_id}/requirements/import` | role_gated | project | `admin` · `tenant_admin` · `project_editor` · `project_owner` | `tenant_manager` | — | 501 |
### c6_validation

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `GET` | `/api/v1/validation/plugins` | authenticated | — | any authenticated | — | — | 200 |
| `GET` | `/api/v1/validation/domains` | authenticated | — | any authenticated | — | — | 200 |
| `POST` | `/api/v1/validation/runs` | role_gated | project | `admin` · `project_editor` · `project_owner` | `tenant_manager` | — | 202 |
| `GET` | `/api/v1/validation/runs/{run_id}` | authenticated | — | any authenticated | — | — | 200 |
| `GET` | `/api/v1/validation/runs/{run_id}/findings` | authenticated | — | any authenticated | — | — | 200 |
| `GET` | `/api/v1/validation/findings/{finding_id}` | authenticated | — | any authenticated | — | — | 200 |
| `GET` | `/api/v1/validation/health` | open | — | *(open)* | — | — | 200 |
### c7_memory

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `POST` | `/api/v1/memory/retrieve` | authenticated | tenant | any authenticated | — | — | 200 |
| `POST` | `/api/v1/memory/projects/{project_id}/sources` | role_gated | project | `admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `c7_sources` | 201 |
| `POST` | `/api/v1/memory/projects/{project_id}/sources/{source_id}/ingest-chunks` | role_gated | project | `admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `memory_chunks` | 201 |
| `POST` | `/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-kg` | role_gated | project | `admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `memory_kg_entities` | 200 |
| `POST` | `/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-structural` | role_gated | project | `admin` · `project_editor` · `project_owner` | `tenant_manager` | arango · `memory_kg_entities` | 200 |
| `GET` | `/api/v1/memory/projects/{project_id}/sources` | authenticated | project | any authenticated | — | — | 200 |
| `GET` | `/api/v1/memory/projects/{project_id}/sources/{source_id}` | authenticated | project | any authenticated | — | — | 200 |
| `GET` | `/api/v1/memory/projects/{project_id}/sources/{source_id}/blob` | authenticated | project | any authenticated | — | — | 200 |
| `GET` | `/api/v1/memory/projects/{project_id}/kg/summary` | authenticated | project | any authenticated | — | — | 200 |
| `DELETE` | `/api/v1/memory/projects/{project_id}/sources/{source_id}` | role_gated | project | `admin` · `project_owner` | `tenant_manager` | arango · `c7_sources` | 204 |
| `POST` | `/api/v1/memory/entities/embed` | role_gated | tenant | `admin` | `tenant_manager` | — | 201 |
| `GET` | `/api/v1/memory/projects/{project_id}/quota` | authenticated | project | any authenticated | — | — | 200 |
| `POST` | `/api/v1/memory/projects/{project_id}/refresh` | role_gated | project | `admin` | `tenant_manager` | — | 501 |
| `GET` | `/api/v1/memory/refresh/{job_id}` | authenticated | — | any authenticated | — | — | 501 |
| `GET` | `/api/v1/memory/health` | open | — | *(open)* | — | — | 200 |
### c9_mcp

| Method | Path | Auth | Scope | Accepted roles | Excluded | Backend | Status |
|---|---|---|---|---|---|---|---|
| `POST` | `/api/v1/mcp` | authenticated | tenant | any authenticated | — | — | 200 |
| `GET` | `/api/v1/mcp/tools` | authenticated | — | any authenticated | — | — | 200 |
| `GET` | `/api/v1/mcp/health` | open | — | *(open)* | — | — | 200 |

## 4. Maintenance contract

Adding a new HTTP route to any component is a **two-step** change:

1. Implement the route in the component's `router.py` with the appropriate `_require_role(...)` gate.
2. Add an `EndpointSpec` row to `tests/e2e/auth_matrix/_catalog.py` describing the route's auth, scope, accepted roles, and backend. Re-run `python ay_platform_core/scripts/checks/generate_test_matrix_doc.py --write requirements/065-TEST-MATRIX.md` to refresh this document.

The coherence test [`tests/coherence/test_route_catalog.py`](../ay_platform_core/tests/coherence/test_route_catalog.py) fails the build if step 2 is skipped.

---

**End of 065-TEST-MATRIX.md v1.**
