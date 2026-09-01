---
document: 900-SPEC-BACKUP-RESTORE
version: 2
path: requirements/900-SPEC-BACKUP-RESTORE.md
language: en
status: draft
derives-from: [D-022, D-018, E-100-002]
---

# Backup & Restore Specification

> **STATUS: draft v1 — first pass.** Derives from **D-022** (logical
> tenant/project backup & restore). Locked decisions from D-022:
> **restore-as-new** (a restore always mints NEW tenant/project ids, never
> overwrites live data), **secrets excluded** from archives (portable, safe to
> download; keys/passwords re-entered after restore), **scope = ArangoDB +
> MinIO** (Gitea git history deferred to v2). Consistency is best-effort
> point-in-time (§3 R-900-012). §5 open questions gate a production rollout.
>
> This is a **logical, per-tenant/per-project export** — NOT cluster disaster
> recovery. Full-cluster DR stays the operator's job via `arangodump` /
> `mc mirror` on C11/C10. This capability produces portable, selectable
> archives scoped to one tenant or one project, for clone / migrate / restore.

---

## 1. Scope & principles

- **Two scopes.** A snapshot is taken at **tenant** granularity (all projects +
  tenant-level rows) or **project** granularity (one project's slice).
- **Logical, not physical.** Data is exported as a filtered logical dump (rows
  by `tenant_id` / `project_id`, objects by bucket + key prefix), never a raw
  volume copy — so an archive is portable across clusters and schema-checked on
  import.
- **Restore-as-new by construction.** A restore creates a fresh tenant/project
  (new ids) and remaps every reference; it can never clobber a live tenant.
- **Secrets never travel.** No password hashes, no encrypted provider keys, no
  session tokens in an archive (R-900-010).
- **Reproducibility alignment (D-018).** C7's derived stores (vectors, KG) are
  projections of MinIO artifacts; a restore MAY rebuild them by replay rather
  than shipping every vector, when the artifact layer is present (R-900-009).

---

## 2. Entities

### E-900-001 — DataMap

```yaml
id: E-900-001
version: 1
status: draft
category: entity
derives-from: [D-022]
```

The authoritative, code-owned enumeration of every persistent store slice that
belongs to a tenant/project, per component. For each entry: the store
(`arango` | `minio`), the collection or bucket, the scoping field
(`tenant_id` / `project_id`) or key-prefix template, the owning component, the
schema version, and a `secret` flag (excluded from archives). The DataMap is
the single source of truth for both snapshot and restore; a store not in the
map is not backed up (and a coherence test SHALL fail the build when a
tenant/project-scoped collection exists with no DataMap entry — R-900-001).

### E-900-002 — BackupManifest

```yaml
id: E-900-002
version: 1
status: draft
category: entity
derives-from: [D-022]
```

The `manifest.json` at the root of every archive. Fields: `manifest_version`,
`scope` (`tenant` | `project`), source `tenant_id` (+ `project_id` for project
scope), `created_at`, `platform_version`, the list of included components with
their `schema_version`, and per entry a row/object `count` and a `sha256`
checksum. The manifest is the contract validated on upload and restore
(R-900-007, R-900-009).

### E-900-003 — BackupRecord

```yaml
id: E-900-003
version: 1
status: draft
category: entity
derives-from: [D-022]
```

A registry row (ArangoDB `backup_records`) describing one stored archive:
`backup_id`, `scope`, `tenant_id` (+ `project_id`), `created_at`, `created_by`,
`size_bytes`, `object_key` (its location in the backups bucket), `checksum`,
`origin` (`generated` | `uploaded`), and `manifest_version`. Never holds the
archive bytes — only its metadata + pointer.

---

## 3. Requirements

### R-900-001 — Authoritative data map

```yaml
id: R-900-001
version: 1
status: draft
category: functional
derives-from: [D-022, E-900-001]
```

The platform SHALL maintain a code-owned **DataMap** (E-900-001) enumerating
every tenant/project-scoped ArangoDB collection and MinIO bucket/prefix, its
owning component, its scoping field(s), its schema version, and whether it is a
`secret` store. A coherence test SHALL assert that every ArangoDB collection
carrying a `tenant_id` field and every per-tenant MinIO bucket appears in the
DataMap (or is explicitly listed as intentionally-excluded, with a reason), so
a new component's store cannot silently escape backup.

### R-900-002 — Snapshot creation

```yaml
id: R-900-002
version: 1
status: draft
category: functional
derives-from: [D-022, R-900-001, R-900-010]
```

The platform SHALL create a snapshot for a given scope (tenant or project). It
SHALL stream, per DataMap entry, the rows filtered by `tenant_id`
(+ `project_id`) and the MinIO objects under the entry's key prefix, into a
`tar.gz` (R-900-003), writing a `manifest.json` (E-900-002) with per-entry
counts + checksums. Secret stores/fields SHALL be omitted (R-900-010). The
operation runs as an async job (it MAY be large); its `BackupRecord`
(E-900-003) is persisted on completion.

### R-900-003 — Archive format

```yaml
id: R-900-003
version: 1
status: draft
category: functional
derives-from: [D-022, E-900-002]
```

An archive SHALL be a gzip-compressed tar with this layout:
`manifest.json` at the root; `arango/<collection>.jsonl` (one JSON document per
line, `_key`/`_id`/`_rev`/`_from`/`_to` handled per §4); `minio/<bucket>/<key>`
preserving object keys verbatim. The format SHALL be self-describing via the
manifest so an archive is restorable without out-of-band knowledge.

### R-900-004 — Dedicated, isolated backup zone

```yaml
id: R-900-004
version: 1
status: draft
category: functional
derives-from: [D-022, E-100-002]
```

Archives SHALL be stored in a dedicated MinIO bucket (`backups`), keyed
`tenant/<tenant_id>/<backup_id>.tar.gz` for tenant scope and
`tenant/<tenant_id>/project/<project_id>/<backup_id>.tar.gz` for project scope.
Access SHALL be tenant-isolated: a caller SHALL only list/download/restore
archives of tenants/projects it is authorized for (R-900-011). The backups
bucket SHALL be separate from every component's operational bucket.

### R-900-005 — List snapshots

```yaml
id: R-900-005
version: 1
status: draft
category: functional
derives-from: [D-022, R-900-004]
```

The platform SHALL list the `BackupRecord`s visible to the caller, filtered by
scope + authorization, newest first, with size, origin, created_by, and
manifest/schema versions surfaced for selection.

### R-900-006 — Download archive

```yaml
id: R-900-006
version: 1
status: draft
category: functional
derives-from: [D-022, R-900-004]
```

The platform SHALL let an authorized caller download a stored archive as
`application/gzip` (`.tar.gz`), by `backup_id`. The download SHALL stream (no
full in-memory buffering) to support large archives.

### R-900-007 — Upload archive

```yaml
id: R-900-007
version: 1
status: draft
category: functional
derives-from: [D-022, E-900-002, R-900-013]
```

The platform SHALL accept an uploaded `.tar.gz` archive, validate its
`manifest.json` (well-formed, `manifest_version` supported, per-entry checksums
match the payload), store it in the backup zone (R-900-004) as a
`BackupRecord` with `origin=uploaded`, and make it selectable for restore. A
malformed/checksum-mismatched/unsupported-version archive SHALL be rejected
with a precise reason (never partially imported).

### R-900-008 — Restore-as-new

```yaml
id: R-900-008
version: 1
status: draft
category: functional
derives-from: [D-022, R-900-001, R-900-010]
```

Restoring an archive SHALL create a NEW target: a new `tenant_id` (tenant
scope) or a new `project_id` under a chosen existing tenant (project scope). It
SHALL remap every occurrence of the source id(s) — including cross-collection
references (`_from`/`_to` edges, `project_id`/`tenant_id` foreign fields,
MinIO key prefixes) — to the new id(s), preserving referential integrity. It
SHALL NEVER write into an existing populated tenant/project (no overwrite,
no merge). Secret fields land empty (R-900-010).

### R-900-009 — Restore validation & dry-run

```yaml
id: R-900-009
version: 1
status: draft
category: functional
derives-from: [D-022, E-900-002, R-900-013]
```

Before committing a restore, the platform SHALL validate the archive against
the current schema: `manifest_version` supported, each component's
`schema_version` compatible (or a documented migration applied), per-entry
checksums verified, and referential integrity of the id-remap plan checked
(no dangling `_from`/`_to`). A **dry-run** mode SHALL report the full plan
(components, counts, new ids, any incompatibility) WITHOUT writing. A restore
that fails validation SHALL abort atomically at the target (the new
tenant/project is rolled back / never left half-populated).

### R-900-010 — Secrets exclusion

```yaml
id: R-900-010
version: 1
status: draft
category: security
derives-from: [D-022, E-100-002]
```

An archive SHALL NOT contain secrets. The excluded set SHALL be explicit in the
DataMap and SHALL include at least: C2 password hashes and any credential
material, C8 embedding/LLM provider `api_key_ciphertext` + hints, session
tokens, and the `AY_SECRET_MASTER_KEY`. Rows whose only sensitive part is a
field SHALL be exported with that field blanked (not dropped). After a restore,
these SHALL be empty/`not_set`, requiring re-entry by an operator; the UX SHALL
surface which credentials need re-provisioning.

### R-900-011 — Authorization & isolation

```yaml
id: R-900-011
version: 1
status: draft
category: security
derives-from: [D-022, E-100-002]
```

Access to every backup operation (snapshot / list / download / upload /
restore) SHALL be gated by the caller's role AGAINST the backup's scope, per
this hierarchy:

- **`platform_manager` (super user)** — full access to ALL backups across ALL
  tenants and projects: export (snapshot/download) and import (upload/restore)
  anything.
- **`tenant_admin` (tenant manager)** — the same operations, but scoped to the
  projects of THEIR tenant only. May snapshot/list/download/upload/restore any
  project of their tenant; SHALL NOT see or touch another tenant's backups.
- **`project_owner`** — the same operations, but scoped to THEIR project only.

Cross-tenant / cross-project isolation SHALL hold on every operation (a caller
SHALL NOT enumerate, download, or restore a backup outside their authorised
scope — enforced by matching the backup's `tenant_id`/`project_id` against the
caller's grants). Restore, being destructive-adjacent (creates entities,
consumes quota), SHALL require an explicit confirmation and SHALL be recorded
in the audit trail. Isolation is enforced at THIS service layer (the backups
bucket is not per-tenant cryptographically isolated — see Q-900-005); the
bucket is never publicly exposed and is reachable only through these gated
endpoints (or the shared MinIO credentials, a platform secret).

### R-900-012 — Consistency semantics

```yaml
id: R-900-012
version: 1
status: draft
category: non-functional
derives-from: [D-022]
```

A snapshot is **best-effort point-in-time**: it is NOT a globally atomic
cross-store transaction. Each collection SHALL be read with a single consistent
cursor; MinIO objects are listed then streamed. Concurrent writes during a
snapshot MAY produce minor cross-store skew; the manifest SHALL record
`created_at` and per-entry counts so skew is detectable. v1 does NOT quiesce
writes; a "consistent (quiesced) snapshot" mode is a v2 open question
(Q-900-004).

### R-900-013 — Integrity & idempotency

```yaml
id: R-900-013
version: 1
status: draft
category: functional
derives-from: [D-022, E-900-002]
```

Every archive entry SHALL carry a `sha256`; upload (R-900-007) and restore
(R-900-009) SHALL verify them and reject on mismatch. Manifest counts SHALL be
re-verified on restore (actual rows/objects imported == manifest counts).
A restore SHALL be idempotent-safe: re-running a failed restore starts a fresh
target (restore-as-new), never continues a corrupted one.

---

## 4. Component C16 — Backup/Restore Service

A new stateless backbone component (`c16_backup`), packaged from the shared
`Dockerfile.api` (`COMPONENT_MODULE=c16_backup`, per R-100-114), behind C1
forward-auth. It owns NO domain data of its own beyond the `backup_records`
registry; it reads C11 (ArangoDB) + C10 (MinIO) by DataMap and writes archives
to the `backups` bucket. Reaching into other components' collections is the
one sanctioned exception to the "no cross-component store access" rule
(R-100-012), justified by backup being an inherently cross-cutting concern and
mediated entirely by the DataMap (R-900-001).

**HTTP surface** (all `project_id`/`tenant_id` scoped, forward-auth):

| Method + path | Purpose | Gate |
|---|---|---|
| `POST /api/v1/backups/snapshot` | Create a snapshot (body: scope + ids) → async job → `BackupRecord` | owner / tenant_admin per scope |
| `GET  /api/v1/backups` | List records (scope-filtered) | as above (read) |
| `GET  /api/v1/backups/{backup_id}/download` | Stream the `.tar.gz` | as above |
| `POST /api/v1/backups/archives` | Upload a `.tar.gz` (validate + register) | as above |
| `POST /api/v1/backups/{backup_id}/restore` | Restore-as-new (body: target tenant/new-project + `dry_run`) | tenant_admin / platform_manager |

Contracts (schemas E-900-001..003 + the request/response models) SHALL be
registered in `contract_registry.py`; routes SHALL be catalogued in the
auth-matrix `_catalog.py` (§13 of CLAUDE.md) and mirrored in the C1 ingress
with the `/api/v1/backups` prefix (forward-auth), with a
`test_route_declarations.py` guard.

---

## 5. Open questions

| Id | Question | Resolution |
|---|---|---|
| Q-900-001 | Component number/shape: dedicated `c16_backup` service vs a module of C8-admin. Draft assumes a new C16 (C14/C16+ are free). | proposed C16 standalone; confirm |
| Q-900-002 | Gitea per-project git history in archives. | v2 (D-022 scopes v1 to Arango+MinIO) |
| Q-900-003 | Overwrite / in-place restore (true rollback of a live tenant). | v2 (v1 is restore-as-new only) |
| Q-900-004 | Quiesced (globally-consistent) snapshot mode. | v2 (v1 best-effort point-in-time) |
| Q-900-005 | Encryption-at-rest of archives in the backups bucket (they exclude secrets but still hold business data). | v1 relies on bucket ACL + tenant isolation; envelope-encryption a v2 candidate |
| Q-900-006 | Cross-version restore: automatic schema migration when `schema_version` differs. | v1 rejects incompatible; a migration path is v2 |
| Q-900-007 | Retention / GC of old archives + size quotas per tenant. | v1 keeps all; retention policy TBD |
| Q-900-008 | Tenant restore-as-new: USER identity remapping. v1 remaps the tenant id + every project id (the bulk of data), but user accounts / grants / preferences (keyed by a shared `user_id`) are captured in the archive without their identities being remapped on restore — a restored tenant's users need re-provisioning. A third id dimension (fresh user ids + rewrite of `user_id` fields + `c2_user_preferences._key`) is the v2 fix. | v2 |

---

## 6. Verification strategy (near-formal)

Per the operator directive, the capability SHALL be validated with dedicated
datasets and exhaustive checks across all tiers:

- **Unit** (`tests/unit/c16_backup/`): DataMap completeness against a fixture
  schema; manifest build/parse; checksum + count integrity; id-remap planner
  (referential integrity, `_from`/`_to`, key prefixes); secret-exclusion filter;
  archive tar/gz read-write round-trip; authz gate logic.
- **Contract** (`tests/contract/c16_backup/`): request/response + manifest
  schema stability; registry entries; route catalog + ingress guard.
- **Integration** (`tests/integration/c16_backup/`, real ArangoDB + MinIO):
  the **round-trip property** — seed a dedicated tenant/project dataset spanning
  EVERY DataMap store (C2 project + members, C3 conversation + messages, C4 run
  + artifacts, C5 requirements doc + entities, C6 validation, C7 sources +
  chunks + KG + vectors, C8 catalogue + selection), snapshot it, restore-as-new,
  then assert the restored target is **deep-equal to the source modulo (a) the
  remapped ids and (b) the excluded secrets** (which SHALL be empty). Plus:
  per-store coverage (no store silently missed), referential integrity of the
  restored graph, checksum-mismatch rejection, upload of a hand-crafted archive,
  dry-run reports without writing, and no-overwrite (restore refuses a populated
  target).
- **E2E** (`tests/e2e/`): the full cross-component flow over HTTP — an
  authorized caller snapshots a project → lists → downloads → re-uploads →
  restores-as-new → the new project is usable (its RAG retrieves the restored
  sources, its requirements resolve) — with the auth-matrix asserting the
  role/isolation gates on every backup endpoint.

The round-trip deep-equality (source ≡ restore modulo ids+secrets) is the
**formal-ish invariant** that pins correctness; every DataMap entry SHALL be
exercised by the seeded dataset so coverage is exhaustive by construction.
