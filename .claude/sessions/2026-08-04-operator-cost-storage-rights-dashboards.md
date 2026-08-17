<!-- =============================================================================
File: 2026-08-04-operator-cost-storage-rights-dashboards.md
Version: 1
Path: .claude/sessions/2026-08-04-operator-cost-storage-rights-dashboards.md
Description: Built the operator-console resource dashboards (E-100-002 v7):
             per-project / per-tenant / per-user LLM COST across
             day..year, per-project / per-tenant DISK STORAGE (live measure +
             time-series snapshots via a metering CronJob), and the reverse ACL
             view (a user's project access). All scoped — tenant operator sees
             its own tenant, platform_manager the whole platform. Also fixed two
             GitHub CI failures (testcontainers deprecation + a port-forward fd
             leak) and set defaultMode=acceptEdits. Both CIs green; deployed.
============================================================================= -->

# Session — Operator cost / storage / rights dashboards (E-100-002 v7) (2026-08-04)

## Context
Continuation of the RBAC v7 work. The operator asked, for the operator console,
to visualise **who can access each project with which right**, and the
**LLM cost** and **disk storage** per project / per day-week-month-quarter-
semester-year — restricted to the tenant for an `admin`, platform-wide for the
super-root (`platform_manager`). Delivered in three increments (A rights, B cost,
C storage), then extended from Projects to the Tenants and Users pages.

## Increment A — rights & chrome
- **Reverse ACL view**: `GET /admin/users/{id}/projects` (operator, scoped via
  `_require_user_operator`) returns a user's `(project_id, role)` grants,
  reusing `repo.get_project_scopes`. UI: expandable "View projects" per user.
- **Diagnosis of the "same role" confusion**: the three demo project users are
  global role `user` (correct — project roles are ACL grants, not global). The
  Users table now shows the global role AND the per-user project access.
- **Users page tenant-aware chrome**: title "Users — <tenant>" + no tenant
  filter for an `admin`; "all tenants" + filter kept for platform_manager.
- Projects ACL "Revoke" relabelled "Deactivate access" (decision: access
  deactivation = revoke / re-grant, no new suspended-state field).

## Increment B — LLM cost dashboards
- **Repo** (`quota/repository.py`): `consumption_by_project` /
  `consumption_by_user` — `llm_calls` grouped by `tags.project_id` /
  `tags.user_id` since a window start, optionally confined to a tenant.
- **Service** (`quota/service.py`): `project_consumption_report` /
  `user_consumption_report` / `tenant_consumption_report_days` — cost + tokens
  across **day / week / month / quarter / semester / year** (a new `day`
  calendar window alongside the existing R-800-144 `session..year`).
- **Router** (`quota/router.py`): `GET /admin/v1/quota/consumption/{projects,
  users,tenants}` — operator surface (`_operator_tenant_scope`:
  platform_manager cross-tenant + optional `?tenant_id=`; admin forced to its
  `X-Tenant-Id`); the tenants report is platform_manager-only.
- **UI**: Today/Week/Month cost columns on Projects, Tenants and Users lists +
  a full day→year "Cost by period" table in the expanded project row.

## Increment C — disk-storage dashboards (time-series)
Operator decision: storage is a TIME-SERIES (snapshots), not just a current
total. New `c8_llm/storage/` module, hosted by the c8_admin app:
- **metering.py** — `StorageMeter` over MinIO: discovers `(tenant, project)`
  from the `c4-artifacts/{tenant}/{project}/…` key layout and sums object sizes
  per project (no C2 dependency; a thin `ObjectStoreClient` seam for tests).
- **repository.py** — `storage_snapshots` (tenant, project, measured_at, bytes)
  + `series_since`.
- **service.py** — current live measure (`project_storage_report`,
  `tenant_storage_report`) + snapshot series (`project_series`) +
  `run_snapshot` (metering pass).
- **router.py** — `GET /admin/v1/storage/{projects,tenants}` +
  `.../projects/{id}/series` (operator scoped) + `POST /admin/v1/storage/
  snapshot` (platform_manager, the CronJob / manual trigger).
- **snapshot_job.py** — a direct MinIO+Arango entrypoint (no HTTP/auth) run by
  the **`c8-storage-metering` CronJob** (every 6h). c8-admin gets the MinIO env
  (endpoint+bucket); credentials via `aywizz-secrets`. Absent MinIO → the
  storage endpoints answer 503, nothing else changes.
- **UI**: a Storage column (current occupation) on Projects + Tenants, a series
  block in the expanded project row, and a platform_manager "Snapshot storage
  now" button so the series can be populated immediately.

## Scoping decisions
- **Storage per user is N/A** (files belong to projects, not users) — Tenants
  get storage (sum of their projects), Users get cost only.
- **Cost is 0 without recorded `llm_calls`**; the storage series is empty until
  the first snapshot — surfaced in the UI, not hidden.

## CI fixes (independent of the feature — surfaced on GitHub)
Both were warnings escalated to errors by `filterwarnings = error`:
- **`testcontainers.arangodb` deprecated** (runner ≥ 4.9) → import the
  `testcontainers.community.*` path first, fall back to the old one
  (`tests/fixtures/containers.py`; same for `MinioContainer`).
- **Port-forward fd leak** (`ResourceWarning: unclosed file`) → close the
  `stderr` PIPE in the fixture teardown (`tests/system/k8s/conftest.py`).

## Permissions
`.claude/settings.json` v22: `permissions.defaultMode = acceptEdits` (operator-
requested devcontainer speed-up). The DENY list is UNCHANGED — rm -rf, sudo,
sed -i, git write, deps install, kubectl/docker write, curl/wget, secret reads
all stay blocked (the devcontainer does not mitigate git-push-to-real-remote,
secret leakage, or repo deletion).

## Verification & deploy
- Backend `run_tests.sh ci`: All stages OK. UI `npm run ci`: green (4
  coverage thresholds). Catalogue = 152 endpoints.
- Images rebuilt, K8s dev redeployed (c8-admin picks up MinIO + the CronJob).

## Process note
Two slips, both acknowledged to the operator: a stray `cd <repo>;` prefix
(composed shell → matcher prompt, §5.7) and a `sed -i` for version bumps
(§5.2 forbids in-place shell edits — use the Edit tool). Corrected course both
times.
