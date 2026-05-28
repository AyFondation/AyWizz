---
document: 300-SPEC-REQUIREMENTS-MGMT
version: 1
path: requirements/300-SPEC-REQUIREMENTS-MGMT.md
language: en
status: draft
derives-from: [D-001, D-005, D-012]
---

# Requirements Management Specification

> **Purpose of this document.** Specify the Requirements Service (C5): the storage format, organisation, CRUD API, versioning semantics, tailoring enforcement, consistency invariants, and import/export surfaces of the platform's requirements corpus. This spec defines the contract between C5 and every component that consumes requirements (C3, C4, C6, C7, C9).

---

## 1. Purpose & Scope

This document specifies the Requirements Service (C5) of the platform: how requirements are stored, versioned, retrieved, modified, imported, and exported. It establishes:

- The native storage format (Markdown with YAML frontmatter) and the role of StrictDoc as a tooling library.
- The physical organisation of the corpus across MinIO (C10) and ArangoDB (C11).
- The CRUD API contract consumed by other components.
- Versioning and history operations grounded in the methodology.
- Tailoring enforcement (platform → project).
- Consistency invariants between MinIO and ArangoDB, including the reindex operation.
- Concurrency model (optimistic locking via version).
- Import and export operations (formats, atomicity).
- Domain-agnostic design constraints per D-012.

**Out of scope.**
- Embeddings computation and semantic retrieval (→ `400-SPEC-MEMORY-RAG.md`).
- Validation checks applied to the corpus (→ `700-SPEC-VERTICAL-COHERENCE.md`).
- User-facing UI for browsing and editing requirements (→ `500-SPEC-UI-UX.md`; this document defines the API it consumes).
- RBAC model (already specified in E-100-002); this document only references it.
- Git synchronisation with user remotes (→ `400-SPEC-MEMORY-RAG.md` or dedicated ops spec).

---

## 2. Relationship to Synthesis Decisions

| Decision | How this document operationalises it |
|---|---|
| D-001 v2 (StrictDoc tooling adoption) | Defines the Markdown+YAML native format and the adapter layer that feeds StrictDoc's in-memory model. |
| D-005 (platform/project hierarchy with explicit tailoring) | Defines runtime enforcement of the `tailoring-of` + `override` rule and surfaces violations through the API. |
| D-012 (domain extensibility) | Keeps the API and storage schema domain-agnostic; `category:` and `domain:` fields carry domain-specific semantics without coupling the API contract. |

---

## 3. Glossary

| Term | Definition |
|---|---|
| **Corpus** | The full set of requirements documents managed by the platform for a given project (or at platform level). |
| **Entity** | A single addressable unit in the corpus (requirement, validation artifact, etc.) as defined in `meta/100-SPEC-METHODOLOGY.md` §3.1. |
| **Document** | A single Markdown file hosting one or more entities. |
| **Source of truth** | The `.md` file in MinIO (C10), bytes-exact as edited. |
| **Projected model** | The in-memory StrictDoc-compatible representation of the corpus, rebuildable from source. |
| **Derived index** | The metadata and relation graph stored in ArangoDB (C11), kept consistent with the source of truth. |
| **Optimistic locking** | Concurrency control based on attaching the expected current version to each update; rejection on mismatch. |
| **Idempotency key** | A client-provided token on mutating requests to allow safe retries without duplicate effects. |
| **Reindex** | The operation that rebuilds the derived index from the source of truth without service interruption. |

---

## 4. Functional Requirements

### 4.1 Storage format & StrictDoc role

#### R-300-001

```yaml
id: R-300-001
version: 1
status: draft
category: functional
```

The native on-disk storage format for all requirements documents SHALL be **Markdown with YAML frontmatter**, as defined in `meta/100-SPEC-METHODOLOGY.md` §3.3 (document-level frontmatter) and §3.4 (entity-level frontmatter).

**Rationale.** Per D-001 v2: consistency with the platform's own specs; native rendering in Git forges and IDEs; parseable YAML frontmatter for extended fields without custom DSL.

#### R-300-002

```yaml
id: R-300-002
version: 1
status: draft
category: functional
```

The Requirements Service SHALL NOT persist documents in StrictDoc's native `.sdoc` format at rest. StrictDoc is consumed as a Python library for validation, traceability matrices, relation graph analysis, and HTML export only.

**Rationale.** Per D-001 v2: StrictDoc is a tooling library, not the storage format. Persisting in `.sdoc` would require a custom grammar file and duplicate the source of truth.

#### R-300-003

```yaml
id: R-300-003
version: 1
status: draft
category: functional
```

The Requirements Service SHALL provide an **adapter layer** that converts Markdown+YAML documents into StrictDoc's in-memory model on demand. The adapter SHALL be bidirectional: it SHALL also serialise StrictDoc's in-memory model back to Markdown+YAML when exporting to a StrictDoc-managed workflow.

**Rationale.** The adapter isolates the storage format from StrictDoc's evolving API. Bidirectional support enables future interoperability with teams that adopt StrictDoc natively.

#### R-300-004

```yaml
id: R-300-004
version: 1
status: draft
category: functional
```

The adapter layer SHALL preserve round-trip fidelity: a document read from MinIO, converted to StrictDoc's in-memory model, and serialised back SHALL yield a byte-exact match with the original, except for cosmetic differences explicitly tolerated (line-ending normalisation, trailing whitespace stripping).

**Rationale.** Non-fidelity would corrupt git history (spurious diffs) and defeat the versioning model in `meta/100-SPEC-METHODOLOGY.md`.

#### R-300-005

```yaml
id: R-300-005
version: 1
status: draft
category: functional
```

The Requirements Service SHALL reject on write any document whose frontmatter or entity frontmatter contains fields not listed in `meta/100-SPEC-METHODOLOGY.md` R-M100-040 (document-level) or R-M100-041 (entity-level). Unknown fields SHALL cause a validation error, not a warning.

**Rationale.** Per R-M100-041: unknown fields are rejected. Enforcement happens at the Requirements Service boundary to prevent corrupted writes from reaching the derived index.

---

### 4.2 Corpus organisation

#### R-300-010

```yaml
id: R-300-010
version: 1
status: draft
category: functional
```

Each user project SHALL have a dedicated MinIO bucket at path `projects/<project-id>/requirements/`. Platform-level requirements (the platform's own specs) SHALL live in a dedicated MinIO bucket `platform/requirements/`. Cross-project reads are prohibited; cross-level reads (project → platform) are allowed for tailoring resolution only.

**Rationale.** Strict project scoping aligns with R-100-083 (external sources are project-scoped). Platform-level requirements are globally readable as the parent layer of the hierarchy defined in D-005.

#### R-300-011

```yaml
id: R-300-011
version: 1
status: draft
category: functional
```

Within a project's requirements bucket, documents SHALL be organised according to the following convention:

```
projects/<project-id>/requirements/
├── NNN-SPEC-<slug>.md            (detailed specs)
├── 999-SYNTHESIS.md              (project-level synthesis, optional)
├── CHANGELOG.md                  (optional per project)
└── meta/
    └── NNN-SPEC-<slug>.md        (project-level methodology overrides, optional)
```

The `NNN` numbering follows `meta/100-SPEC-METHODOLOGY.md` R-M100-001 (hundreds with suffix insertion).

**Rationale.** Consistent corpus layout across projects simplifies tooling, onboarding, and cross-project audit.

#### R-300-012

```yaml
id: R-300-012
version: 1
status: draft
category: functional
```

The ArangoDB derived index SHALL include at minimum the following collections, owned exclusively by the Requirements Service (per R-100-012):

- `req_entities` (document collection) — one record per entity, keyed by `<project-id>:<entity-id>`.
- `req_documents` (document collection) — one record per document, keyed by `<project-id>:<document-slug>`.
- `req_relations` (edge collection) — edges between entities, typed by relation kind (`derives-from`, `impacts`, `tailoring-of`, `supersedes`).
- `req_history` (document collection) — denormalised history pointers for efficient version queries (see R-300-040).

The detailed schema is defined in Appendix 8.1.

**Rationale.** Explicit collection ownership per R-100-012. Edge collection enables efficient graph traversals required by the validation engine and retrieval.

#### R-300-013

```yaml
id: R-300-013
version: 1
status: draft
category: functional
```

The ArangoDB derived index SHALL be **rebuildable entirely from the MinIO source of truth** via the reindex operation (see §4.7). A complete loss of ArangoDB collections SHALL result in recoverable service restoration, not data loss.

**Rationale.** Per R-100-020: MinIO is the source of truth. The derived index is a cache; its integrity is operationally valuable but not authoritative.

---

### 4.3 CRUD API

#### R-300-020

```yaml
id: R-300-020
version: 1
status: draft
category: functional
```

The Requirements Service SHALL expose a REST API following the resource-oriented style, rooted at `/api/v1/projects/{project_id}/requirements/`. HTTP verbs SHALL map to operations as:
- `GET` — read operations (lists, individual documents, individual entities).
- `POST` — creation.
- `PUT` — full replacement of a document or entity.
- `PATCH` — partial update of an entity's frontmatter or body.
- `DELETE` — deletion (soft, see §4.4 for semantics).

**Rationale.** Resource-oriented REST is familiar to clients, well-tooled, and maps cleanly to MCP tool shapes (per D-003, §6.2 of this document).

#### R-300-021

```yaml
id: R-300-021
version: 1
status: draft
category: functional
```

Every mutating request (`POST`, `PUT`, `PATCH`, `DELETE`) SHALL accept an `Idempotency-Key` HTTP header. The Requirements Service SHALL cache idempotency keys for at least 24 hours and SHALL return the cached response for a repeated key within that window, instead of re-executing the operation.

**Rationale.** Network retries are routine in orchestrated environments (K8s, n8n). Idempotency keys prevent duplicate creations and double-applies.

#### R-300-022

```yaml
id: R-300-022
version: 1
status: draft
category: functional
```

Entity updates (`PUT`, `PATCH` targeting an entity) SHALL require the client to include the expected current version in an `If-Match` HTTP header, formatted as `"<entity-id>@v<N>"`. The server SHALL return HTTP 412 Precondition Failed if the current version does not match. The error body SHALL include the current version, enabling the client to re-read and retry.

**Rationale.** Optimistic locking (Qc option b). Prevents lost updates from concurrent editors without the UX cost of pessimistic locks.

#### R-300-023

```yaml
id: R-300-023
version: 1
status: draft
category: functional
```

Document updates (`PUT` targeting a document) SHALL also require an `If-Match` header, formatted as the document-level version. The semantics match entity-level optimistic locking.

**Rationale.** Whole-document replacements are coarse operations; optimistic locking applies uniformly.

#### R-300-024

```yaml
id: R-300-024
version: 1
status: draft
category: functional
```

The following endpoint set SHALL be exposed (minimum v1 surface):

```
GET    /api/v1/projects/{pid}/requirements/documents
GET    /api/v1/projects/{pid}/requirements/documents/{doc-slug}
PUT    /api/v1/projects/{pid}/requirements/documents/{doc-slug}
POST   /api/v1/projects/{pid}/requirements/documents
DELETE /api/v1/projects/{pid}/requirements/documents/{doc-slug}

GET    /api/v1/projects/{pid}/requirements/entities
GET    /api/v1/projects/{pid}/requirements/entities/{entity-id}
PATCH  /api/v1/projects/{pid}/requirements/entities/{entity-id}
DELETE /api/v1/projects/{pid}/requirements/entities/{entity-id}

GET    /api/v1/projects/{pid}/requirements/entities/{entity-id}/history
GET    /api/v1/projects/{pid}/requirements/entities/{entity-id}/versions/{v}

GET    /api/v1/projects/{pid}/requirements/relations?source={eid}&type={rel-type}

POST   /api/v1/projects/{pid}/requirements/import
GET    /api/v1/projects/{pid}/requirements/export?format={md|reqif}

POST   /api/v1/projects/{pid}/requirements/reindex
GET    /api/v1/projects/{pid}/requirements/reindex/{job-id}
```

The full OpenAPI schema is defined in E-300-001.

**Rationale.** Covers the complete lifecycle. Entity-level endpoints exist alongside document-level endpoints because some clients (pipeline agents, MCP tools) operate at entity granularity while others (bulk edits, imports) operate at document granularity.

#### R-300-025

```yaml
id: R-300-025
version: 1
status: draft
category: functional
```

List endpoints (`GET .../documents`, `GET .../entities`) SHALL support pagination via cursor-based mechanism (`cursor` query parameter and `X-Next-Cursor` response header). Maximum page size SHALL be 100 items; default 50. Offset-based pagination (`offset`/`limit`) SHALL NOT be supported.

**Rationale.** Cursor-based pagination is stable under concurrent inserts; offset-based is not. 100-item cap protects against accidental full-corpus dumps.

#### R-300-026

```yaml
id: R-300-026
version: 1
status: draft
category: functional
```

List endpoints SHALL support filtering by `status:`, `category:`, `domain:`, and free-text search over entity ID and body (case-insensitive substring match). Filters combine conjunctively. Semantic search is explicitly out of scope of this API and is served by the Memory Service (C7).

**Rationale.** Structural filtering is a first-class need of tooling (finding deprecated entities, entities of a category, etc.). Semantic retrieval is a different concern handled elsewhere.

#### R-300-027

```yaml
id: R-300-027
version: 1
status: draft
category: functional
```

Write operations SHALL enforce authorization per E-100-002: the caller SHALL hold `project_editor` or `project_owner` scope on the target project for `POST`, `PUT`, `PATCH`; `project_owner` only for `DELETE` of documents; `project_editor` for entity-level `DELETE`. Platform-level writes SHALL require the `admin` global role.

**Rationale.** Consistent with the RBAC model. Document-level deletion is more destructive than entity-level and requires stronger authorisation.

---

### 4.4 Versioning & history operations

#### R-300-030

```yaml
id: R-300-030
version: 1
status: draft
category: functional
```

On every successful entity update, the Requirements Service SHALL persist the pre-update entity state as a historical snapshot. Snapshots are addressable by `<entity-id>@v<N>` where `N` is the version that was current before the update.

**Rationale.** Enables `R-M100-071` (`requirements-history` tool) and `R-M100-080` (versioned references). History is essential for audit and for the `check #7` drift detection in the vertical coherence engine.

#### R-300-031

```yaml
id: R-300-031
version: 1
status: draft
category: functional
```

Historical snapshots SHALL be stored in MinIO alongside the source documents, in a sibling path `projects/<pid>/requirements/_history/<doc-slug>/<entity-id>@v<N>.md`. Historical snapshots SHALL NOT be modifiable; attempts to write to `_history/` paths SHALL be rejected.

**Rationale.** Storing snapshots in MinIO (not only in ArangoDB) preserves history even in the degenerate case of full ArangoDB loss. Immutability protects audit.

#### R-300-032

```yaml
id: R-300-032
version: 1
status: draft
category: functional
```

The `GET .../entities/{eid}/history` endpoint SHALL return the complete version history of an entity, ordered chronologically, with for each version: the version number, the timestamp, the actor (user id from the JWT), and the semantic change summary extracted from the commit message or PR description if available.

**Rationale.** Auditable change log per entity. Critical for safety/security/regulatory entities (R-M100-126).

#### R-300-033

```yaml
id: R-300-033
version: 1
status: draft
category: functional
```

Entity deletion SHALL be **soft**: the entity's `status:` SHALL transition to `deprecated` (or `superseded` if a `supersedes:` link is provided in the request), and the entity SHALL remain in the corpus per `meta/100-SPEC-METHODOLOGY.md` R-M100-091. Hard deletion is not exposed via the API.

**Rationale.** Per R-M100-091: terminal states are preserved. Hard deletion would break historical references.

#### R-300-034

```yaml
id: R-300-034
version: 1
status: draft
category: functional
```

Document deletion SHALL cascade: all entities in the deleted document transition to `deprecated` with `deprecated-reason: "Document deleted"`. The document's source file is moved to `projects/<pid>/requirements/_deleted/<doc-slug>.md` with a timestamp suffix, preserving the historical content but removing it from the active listing.

**Rationale.** Consistent with entity soft-deletion semantics. The `_deleted/` path acts as a grave, not a trash (no restore in v1).

#### R-300-040

```yaml
id: R-300-040
version: 1
status: draft
category: functional
```

The `req_history` collection in ArangoDB SHALL store version pointers indexed by entity ID and version number, enabling O(1) resolution of any `<entity-id>@v<N>` reference to its MinIO snapshot path. The collection SHALL NOT duplicate the entity bodies; it SHALL point to the authoritative MinIO path.

**Rationale.** Keeps ArangoDB lightweight; avoids doubling storage; respects the source-of-truth principle.

---

### 4.5 Tailoring enforcement

#### R-300-050

```yaml
id: R-300-050
version: 1
status: draft
category: functional
```

When an entity is created or updated with a `tailoring-of: <R-XXX>` field, the Requirements Service SHALL verify that:

1. The target entity `<R-XXX>` exists in the platform-level corpus.
2. The target entity is not in `deprecated` status.
3. The current entity carries `override: true`.
4. The current entity's body contains a subsection whose heading starts with "Tailoring rationale" (case-insensitive).

If any condition is unmet, the write SHALL be rejected with HTTP 422 Unprocessable Entity and an explanatory error body listing the failed checks.

**Rationale.** Per D-005 and R-M100-100 to R-M100-102: explicit tailoring with justification is the only legitimate form of divergence. Violations must be prevented at write time to avoid polluting the corpus.

#### R-300-051

```yaml
id: R-300-051
version: 1
status: draft
category: functional
```

A project-level entity SHALL NOT carry `tailoring-of:` pointing to another project-level entity. Tailoring is strictly platform → project, per R-M100-103. Violations SHALL be rejected with HTTP 422.

**Rationale.** Prevents ad-hoc tailoring chains within a project, which would dilute the platform/project hierarchy semantics.

#### R-300-052

```yaml
id: R-300-052
version: 1
status: draft
category: functional
```

The Requirements Service SHALL expose a `GET .../requirements/tailorings` endpoint returning the list of active tailorings in a project, each with: the project entity ID and version, the platform parent ID and version, the rationale subsection content (excerpted to the first 500 chars), and the current conformity status (conformant, stale-parent, missing-rationale).

**Rationale.** Audit surface. Regulated contexts require the ability to enumerate all divergences from platform rules with their justifications.

---

### 4.6 Consistency & concurrency

#### R-300-060

```yaml
id: R-300-060
version: 1
status: draft
category: functional
```

Every mutating operation SHALL follow a **write-through synchronous pattern**: the Requirements Service SHALL (1) write the updated source to MinIO, (2) update the ArangoDB derived index, (3) publish a NATS event, and (4) return success to the caller. Steps 1, 2, and 3 SHALL all complete before the HTTP response is sent.

**Rationale.** Chosen trade-off (Qb mixed proposal): synchronous writes guarantee that the next read sees the update; asynchronous propagation would expose confusing read-after-write inconsistencies.

#### R-300-061

```yaml
id: R-300-061
version: 1
status: draft
category: functional
```

If the ArangoDB update (step 2) fails after the MinIO write (step 1) succeeded, the Requirements Service SHALL: (1) log a structured error with the entity ID and the intended change, (2) enqueue a reconciliation job, (3) return HTTP 500 to the caller. The MinIO state remains consistent (source of truth), and the derived index is eventually reconciled.

**Rationale.** Two-phase commit across MinIO and ArangoDB is not available. Accepting the partial failure and reconciling is the pragmatic v1 approach. Rarity of the failure mode plus explicit reconciliation makes it acceptable.

#### R-300-062

```yaml
id: R-300-062
version: 1
status: draft
category: functional
```

If the MinIO write (step 1) fails, the operation SHALL be aborted immediately and HTTP 503 returned. No ArangoDB update or NATS publication SHALL occur.

**Rationale.** MinIO is the source of truth; failing to persist means the update didn't happen. No partial state propagates.

#### R-300-063

```yaml
id: R-300-063
version: 1
status: draft
category: functional
```

The Requirements Service SHALL provide a periodic **reconciliation worker** that scans for inconsistencies between MinIO and ArangoDB (missing entities in the index, stale versions, orphaned index records) and repairs them. Default cadence: every 15 minutes. Reconciliation SHALL be observable via Prometheus metrics (`req_reconcile_discrepancies_total`, `req_reconcile_repairs_total`).

**Rationale.** Belt-and-suspenders approach. Combined with the on-demand reindex (§4.7), this ensures convergence even in the presence of partial failures.

#### R-300-064

```yaml
id: R-300-064
version: 1
status: draft
category: functional
```

The Requirements Service SHALL NOT use pessimistic locks (no row-level locks, no distributed mutex) in v1. Concurrency is controlled exclusively through optimistic locking (R-300-022) and idempotency keys (R-300-021).

**Rationale.** Pessimistic locks complicate horizontal scaling (R-100-003 statelessness) and degrade UX. Optimistic locking is sufficient for the expected concurrency profile (low contention per entity).

---

### 4.7 Reindex operation

#### R-300-070

```yaml
id: R-300-070
version: 1
status: draft
category: functional
```

The Requirements Service SHALL expose a `POST .../reindex` operation that triggers a complete rebuild of the ArangoDB derived index for a given project (or platform level). The operation is asynchronous: it returns a job ID immediately, and the client polls `GET .../reindex/{job-id}` for progress and completion.

**Rationale.** Required by R-100-022. Full rebuild is inherently long-running (minutes to tens of minutes for large corpora); asynchronous execution is the only viable pattern.

#### R-300-071

```yaml
id: R-300-071
version: 1
status: draft
category: functional
```

The reindex operation SHALL be **online**: during reindex execution, the Requirements Service SHALL continue serving reads (from the existing index) and SHALL accept writes (which are applied to both the current index and the rebuild in progress). No service interruption SHALL occur.

**Rationale.** Required by R-100-022. A reindex that takes the platform offline is not acceptable for production-grade service.

#### R-300-072

```yaml
id: R-300-072
version: 1
status: draft
category: functional
```

The reindex operation SHALL be idempotent: invoking it twice in succession SHALL produce the same final state. The second invocation SHALL detect the in-progress job and return the existing job ID rather than starting a new one.

**Rationale.** Retries and accidental double-invocations are inevitable in automated workflows.

#### R-300-073

```yaml
id: R-300-073
version: 1
status: draft
category: functional
```

Reindex execution SHALL be authorised: only callers with the `admin` global role or `project_owner` scope on the target project SHALL be allowed to trigger it.

**Rationale.** Reindex is resource-intensive and should not be invokable by unprivileged users.

---

### 4.8 Import / Export

#### R-300-080

```yaml
id: R-300-080
version: 1
status: draft
category: functional
```

The Requirements Service SHALL support import of a complete document set in two formats in v1:
- **Markdown + YAML** (`format=md`) — the platform's native format; the import is a bulk upload of `.md` files.
- **ReqIF** (`format=reqif`) — the OMG ReqIF 1.2 standard; the import converts ReqIF structures to the native Markdown + YAML format.

Other formats (DOCX, CSV, XLSX) are explicitly out of scope for v1.

**Rationale.** Markdown is natively the platform's format; ReqIF is the industry standard for regulated contexts (automotive, aerospace) and aligns with the user's ISO 21434 background. Other formats are deferred until concrete demand emerges.

#### R-300-081

```yaml
id: R-300-081
version: 1
status: draft
category: functional
```

Import SHALL be **atomic per request**: either all documents in the import package are successfully ingested, or none are. Partial imports SHALL NOT leave the corpus in a mixed state.

**Rationale.** Qe option b: atomic whole-document-set operations. Partial state is worse than failure; the caller can retry a clean import.

#### R-300-082

```yaml
id: R-300-082
version: 1
status: draft
category: functional
```

On import, the Requirements Service SHALL validate every incoming entity against the methodology rules (`meta/100-SPEC-METHODOLOGY.md`): entity schema, ID format, tailoring rules, supersession chains. Validation failures SHALL abort the import and return a detailed report of all violations. No partial writes SHALL occur.

**Rationale.** Strict validation prevents garbage-in imports from corrupting the corpus.

#### R-300-083

```yaml
id: R-300-083
version: 1
status: draft
category: functional
```

Import SHALL accept two modes via an `on_conflict` parameter:
- `fail` (default) — any incoming entity whose ID already exists in the target project causes import abortion.
- `replace` — existing entities are replaced by incoming ones; the prior state is captured in history per §4.4.

A third mode `merge` (fine-grained per-field) is explicitly out of scope for v1.

**Rationale.** `fail` protects against accidental overwrites; `replace` supports legitimate bulk updates. `merge` is subtle and risky; defer until a concrete use case arises.

#### R-300-084

```yaml
id: R-300-084
version: 1
status: draft
category: functional
```

Export SHALL produce a complete snapshot of the target project's requirements corpus in either Markdown + YAML (native) or ReqIF format. Export SHALL include all entities (all statuses, including `deprecated` and `superseded`) and all relations. The export is read-only; it does not modify the source corpus.

**Rationale.** Complete export is the basis for backups, migrations, and audits. Deprecated and superseded entities are part of the auditable history.

#### R-300-085

```yaml
id: R-300-085
version: 1
status: draft
category: functional
```

Export SHALL be invocable in **point-in-time mode** via an optional `at=<ISO-8601-timestamp>` parameter: the exported corpus SHALL reflect the state as it was at the given timestamp, reconstructed from the history. Point-in-time export SHALL be available for timestamps not older than the corpus creation.

**Rationale.** Regulatory audits often require reconstructing "the state of the requirements on date X". History snapshots enable this; the export surface makes it accessible.

#### R-300-086

```yaml
id: R-300-086
version: 1
status: draft
category: functional
```

Export SHALL stream the response: for large corpora, the export SHALL use chunked transfer encoding or range-based download rather than buffering the complete archive in memory.

**Rationale.** Corpora can grow to hundreds of MB; full buffering is a memory hazard.

---

### 4.9 Domain-agnostic design

#### R-300-090

```yaml
id: R-300-090
version: 1
status: draft
category: architecture
```

The Requirements Service API and storage schema SHALL NOT hard-code vocabulary specific to any production domain. In particular, the API SHALL use generic terms (`entity`, `document`, `validation artifact`) rather than domain-specific ones (`test`, `code`, `function`).

**Rationale.** Per D-012 and R-100-008: backbone contracts are domain-agnostic. Domain specificity lives in the `category:` and `domain:` fields of individual entities, not in the API.

#### R-300-091

```yaml
id: R-300-091
version: 1
status: draft
category: architecture
```

The `domain:` field on `T-` entities (validation artifacts) SHALL be treated as opaque by the Requirements Service: it is stored, indexed for filtering (R-300-026), and returned on reads, but the Requirements Service SHALL NOT interpret its value or apply domain-specific logic based on it.

**Rationale.** Domain interpretation is the concern of the Validation Pipeline Registry (C6), not of the Requirements Service. The Requirements Service is a generic corpus manager.

---

## 5. Non-Functional Requirements

### 5.1 Performance

#### R-300-100

```yaml
id: R-300-100
version: 1
status: draft
category: nfr
```

Single-entity read (`GET .../entities/{eid}`) SHALL complete in under 50 ms p95 for entities in the cached path, under 200 ms p95 including a MinIO fetch.

**Rationale.** Entity reads are on the hot path of the pipeline (every agent reads requirements). Latency directly impacts user-perceived conversation responsiveness.

#### R-300-101

```yaml
id: R-300-101
version: 1
status: draft
category: nfr
```

Single-entity update (`PATCH .../entities/{eid}`) SHALL complete in under 300 ms p95, including MinIO write, ArangoDB update, and NATS publish.

**Rationale.** Writes are less frequent than reads but still interactive; 300 ms is the ceiling for "perceived as instant".

#### R-300-102

```yaml
id: R-300-102
version: 1
status: draft
category: nfr
```

List operations (`GET .../entities` with filters) SHALL complete in under 500 ms p95 for page sizes up to 100 items on corpora up to 10,000 entities.

**Rationale.** Upper bound on corpus size for v1 target deployments. Beyond 10,000 entities, performance tuning is expected.

#### R-300-103

```yaml
id: R-300-103
version: 1
status: draft
category: nfr
```

Reindex operation SHALL process at least 500 entities per minute on the baseline deployment footprint (R-100-106).

**Rationale.** Establishes a predictable reindex duration for capacity planning. A 5,000-entity corpus reindex should complete in about 10 minutes.

### 5.2 Consistency

#### R-300-110

```yaml
id: R-300-110
version: 1
status: draft
category: nfr
```

The Requirements Service SHALL provide **read-your-writes consistency** to a single caller: after a successful write, any subsequent read from the same session SHALL observe the updated state.

**Rationale.** Users expect to see their own changes immediately. Weaker consistency causes confusing UX.

#### R-300-111

```yaml
id: R-300-111
version: 1
status: draft
category: nfr
```

Cross-session consistency is **eventual** within the reconciliation window (R-300-063, 15 minutes by default). In practice, the write-through pattern (R-300-060) yields near-immediate propagation; the 15-minute window is the worst-case bound if reconciliation is required.

**Rationale.** Full strong consistency across sessions is not required at v1 scale and would introduce significant complexity. Eventual consistency with a bounded window is the standard pattern.

### 5.3 Observability

#### R-300-120

```yaml
id: R-300-120
version: 1
status: draft
category: nfr
```

The Requirements Service SHALL emit Prometheus metrics covering at minimum: request rate and latency percentiles per endpoint, write/read ratio, reconciliation discrepancy count, reindex job duration, optimistic lock conflict rate, tailoring violation rate.

**Rationale.** Establishes operational visibility. The specific metrics are chosen to surface the most common incidents (conflict storms, reconciliation backlog, reindex slowness).

#### R-300-121

```yaml
id: R-300-121
version: 1
status: draft
category: nfr
```

Every mutation SHALL be recorded in a structured audit log with: timestamp, actor (user id, JWT jti), project ID, entity ID, operation, before-version, after-version. The audit log SHALL be retained for at least 365 days.

**Rationale.** Regulatory audit requirement. 1-year retention is the conservative default for automotive cybersecurity and ASPICE contexts.

---

## 6. Interfaces & Contracts

### 6.1 REST API (overview)

See R-300-024 for the endpoint list. The complete OpenAPI schema is defined in E-300-001.

### 6.2 MCP tool surface

The MCP Server (C9) SHALL expose the Requirements Service capabilities as MCP tools. The minimum v1 tool set SHALL include:

- `list_documents(project_id, filter)` — enumerate documents.
- `get_document(project_id, doc_slug)` — fetch a document.
- `put_document(project_id, doc_slug, content, if_match)` — replace a document.
- `list_entities(project_id, filter)` — enumerate entities.
- `get_entity(project_id, entity_id, version=None)` — fetch an entity (optionally at a specific version).
- `patch_entity(project_id, entity_id, changes, if_match)` — update an entity.
- `list_relations(project_id, source_id, rel_type)` — traverse relations.
- `import_corpus(project_id, format, payload, on_conflict)` — bulk import.
- `export_corpus(project_id, format, at=None)` — bulk export.

Tool semantics SHALL be identical to the corresponding REST endpoints. The MCP Server SHALL NOT implement its own logic (per R-100-015); it is a thin translation layer.

**Detailed MCP tool schema.** See E-300-002.

### 6.3 NATS events

The Requirements Service SHALL publish events on the following NATS subjects (hierarchical):

- `requirements.{project-id}.document.created`
- `requirements.{project-id}.document.updated`
- `requirements.{project-id}.document.deleted`
- `requirements.{project-id}.entity.created`
- `requirements.{project-id}.entity.updated`
- `requirements.{project-id}.entity.deprecated`
- `requirements.{project-id}.entity.superseded`
- `requirements.{project-id}.import.completed`
- `requirements.{project-id}.export.completed`
- `requirements.{project-id}.reindex.{started|progressed|completed|failed}`

Event payload schema is defined in E-300-003.

### 6.4 Contract-critical entities

#### E-300-001: REST API OpenAPI reference

```yaml
id: E-300-001
version: 1
status: draft
category: architecture
```

The Requirements Service REST API is formally specified via an OpenAPI 3.1 document maintained alongside the implementation. The canonical path is `api/openapi/requirements-service-v1.yaml` in the platform's source repository. This entity references the OpenAPI document; the detailed endpoint schemas (request/response bodies, error codes, example payloads) are normative in the OpenAPI document, not duplicated here.

**Why not inline.** The OpenAPI spec is verbose and changes frequently during implementation; inlining would cause this entity to churn on every minor endpoint refinement. Keeping it in a dedicated YAML file allows standard tooling (redoc, swagger-ui, code generators) to consume it directly.

**Constraints on the OpenAPI document:**
- Every endpoint listed in R-300-024 SHALL have an operation in the OpenAPI document.
- Every `4xx` error SHALL have an example body.
- Authentication SHALL be declared as `bearerAuth` (JWT per E-100-001).
- Idempotency-Key and If-Match headers SHALL be documented on all mutating operations.

#### E-300-002: MCP tool schema

```yaml
id: E-300-002
version: 1
status: draft
category: architecture
```

The MCP tools exposed by C9 for the Requirements Service follow the MCP specification. Each tool declares: name, description, input schema (JSON Schema), output schema (JSON Schema).

Canonical location: `mcp-server/tools/requirements-service.json`.

Tool names, inputs, and outputs SHALL stay in one-to-one correspondence with the REST endpoints per §6.2. Tool versioning follows MCP conventions (tool name includes a version suffix for breaking changes).

#### E-300-003: NATS event payload schema

```yaml
id: E-300-003
version: 1
status: draft
category: architecture
```

All NATS events published by the Requirements Service SHALL share a common envelope:

```json
{
  "event_id": "<uuid>",
  "event_type": "requirements.<project-id>.<object>.<action>",
  "event_version": 1,
  "timestamp": "2025-11-05T14:23:01.123Z",
  "actor": {
    "user_id": "<user-id>",
    "tenant_id": "<tenant-id>",
    "jwt_jti": "<jti>"
  },
  "project_id": "<project-id>",
  "payload": { /* event-specific */ }
}
```

Event-specific payloads follow per-event schemas defined in the OpenAPI document (E-300-001) under the `/events` section.

**Delivery guarantee.** At-least-once (NATS JetStream). Consumers SHALL be idempotent based on `event_id`.

#### E-300-004: Canonical document skeleton

```yaml
id: E-300-004
version: 1
status: draft
category: architecture
```

The canonical skeleton of a requirements document is defined in `meta/100-SPEC-METHODOLOGY.md` §11.2 (the detailed spec template). This entity references that skeleton as the authoritative form. The Requirements Service SHALL validate incoming documents against this structure on import and on update.

---

## 7. Open Questions

| ID | Question | Owning decision | Target resolution |
|---|---|---|---|
| Q-300-001 | Adapter layer implementation: depend on StrictDoc's public Python API, or reimplement the minimal subset needed? Impacts maintenance burden. | D-001 v2 | v1 (during initial C5 implementation) |
| Q-300-002 | Historical snapshot storage: full document copy, or entity-level diff? Full copies are simpler; diffs save storage. | — | v1 (baseline: full document copy; revisit if storage grows) |
| Q-300-003 | Reconciliation worker isolation: per-project worker, or single cluster-wide worker? Scaling and failure-isolation implications. | — | v1 (baseline: cluster-wide with per-project cursor) |
| Q-300-004 | Soft-delete cleanup policy: do `_deleted/` files ever expire? If so, on what schedule? | — | v2 (retention policy is a compliance question) |
| Q-300-005 | ReqIF dialect support: strict OMG ReqIF 1.2, or common extensions (Polarion, IBM DOORS)? | — | v1 (baseline: strict 1.2; extensions per user demand) |
| Q-300-006 | Entity-level RBAC: should `project_editor` be restricted from editing entities they didn't create? (Current R-300-027 allows it.) | — | v2 (project governance question) |
| Q-300-007 | Bulk relation queries: should `GET .../relations` support traversal depth parameters, or rely on C7's graph traversal? | — | v1 (baseline: single-hop only in C5; deeper traversals via C7) |
| Q-300-008 | Handling of very large documents (>1 MB raw): pagination of entity lists within a document? Streaming read? | — | v1 (baseline: soft limit of 1 MB per document, warning above) |
| Q-300-009 | Git backend: does the Requirements Service integrate with per-project Git remotes directly, or strictly through C12 (n8n)? | D-002 | v1 (baseline: strictly through C12; C5 knows nothing of Git) |
| Q-300-010 | Conflict resolution on optimistic lock failure: does the API provide a diff helper, or is that a UI concern? | — | v1 (baseline: UI concern; C5 returns the two versions only) |

---

## 8. Appendices

### 8.1 ArangoDB collection schemas (indicative)

#### Collection `req_entities` (document)

```json
{
  "_key": "<project-id>:<entity-id>",
  "project_id": "<project-id>",
  "entity_id": "<entity-id>",
  "document_slug": "<doc-slug>",
  "type": "R | D | T | E | Q",
  "version": 3,
  "status": "approved",
  "category": "functional",
  "domain": "code",              // optional, per R-M100-042
  "title": "Short title",
  "minio_path": "projects/<pid>/requirements/300-SPEC-REQUIREMENTS-MGMT.md",
  "content_hash": "sha256:...",
  "created_at": "2025-11-05T14:23:01Z",
  "updated_at": "2025-11-05T14:25:17Z",
  "created_by": "<user-id>",
  "updated_by": "<user-id>"
}
```

#### Collection `req_documents` (document)

```json
{
  "_key": "<project-id>:<doc-slug>",
  "project_id": "<project-id>",
  "slug": "300-SPEC-REQUIREMENTS-MGMT",
  "version": 1,
  "language": "en",
  "status": "draft",
  "minio_path": "projects/<pid>/requirements/300-SPEC-REQUIREMENTS-MGMT.md",
  "content_hash": "sha256:...",
  "entity_count": 47,
  "created_at": "2025-11-05T12:00:00Z",
  "updated_at": "2025-11-05T14:25:17Z"
}
```

#### Collection `req_relations` (edge)

Edges from `req_entities/<src>` to `req_entities/<dst>`:

```json
{
  "_from": "req_entities/<project-id>:R-300-001",
  "_to":   "req_entities/<project-id>:R-300-002",
  "type": "derives-from | impacts | tailoring-of | supersedes | superseded-by",
  "version_pinned": 3,         // null if unversioned reference
  "created_at": "2025-11-05T14:23:01Z"
}
```

#### Collection `req_history` (document)

```json
{
  "_key": "<project-id>:<entity-id>:v<N>",
  "project_id": "<project-id>",
  "entity_id": "<entity-id>",
  "version": 2,
  "minio_snapshot_path": "projects/<pid>/requirements/_history/300-SPEC-REQUIREMENTS-MGMT/R-300-001@v2.md",
  "timestamp": "2025-11-05T14:20:00Z",
  "actor": "<user-id>",
  "change_summary": "Refined rationale for tailoring enforcement",
  "commit_ref": "git-sha or pr-number or null"
}
```

Indexes: persistent on `(project_id, entity_id)` in `req_history`; hash on `_key`; edge indexes in `req_relations`.

### 8.2 Reference ReqIF mapping

The following table maps the platform's entity model to ReqIF 1.2 concepts for import/export purposes. Fields without a natural ReqIF equivalent are expressed as ReqIF `ATTRIBUTE-DEFINITION-XHTML` custom attributes.

| Platform concept | ReqIF concept | Notes |
|---|---|---|
| Document | `SPECIFICATION` | One per `N00-SPEC-*.md` |
| Entity (R-*) | `SPEC-OBJECT` | Type `ReqType-Requirement` |
| Entity (D-*) | `SPEC-OBJECT` | Type `ReqType-Decision` |
| Entity (T-*) | `SPEC-OBJECT` | Type `ReqType-Validation` |
| Entity (E-*) | `SPEC-OBJECT` | Type `ReqType-Artifact` |
| Entity (Q-*) | `SPEC-OBJECT` | Type `ReqType-OpenQuestion` |
| `id:` | `IDENTIFIER` | |
| `version:` | Custom attribute `PlatformVersion` | Integer |
| `status:` | `ATTRIBUTE-VALUE-ENUMERATION` | Enum: draft/approved/superseded/deprecated |
| `category:` | Custom attribute `PlatformCategory` | |
| `domain:` | Custom attribute `PlatformDomain` | |
| `derives-from:` | `SPEC-RELATION` type `derives-from` | |
| `impacts:` | `SPEC-RELATION` type `impacts` | |
| `tailoring-of:` | `SPEC-RELATION` type `tailoring-of` | |
| `supersedes:` / `superseded-by:` | `SPEC-RELATION` type `supersedes` | Bidirectional enforced on export |
| Entity body (Markdown) | `ATTRIBUTE-VALUE-XHTML` on main body attribute | Markdown is preserved; consumers may render |
| Tailoring rationale section | Custom attribute `TailoringRationale` on the override target | Text content of the `### Tailoring rationale` subsection |

Round-trip guarantee: import then export yields a ReqIF equivalent to the input up to normalisation of whitespace and attribute ordering. Round-trip between Markdown+YAML and ReqIF is **lossy only on non-semantic formatting** (Markdown decorations in relation descriptions, ReqIF XHTML tag details).

---

**End of 300-SPEC-REQUIREMENTS-MGMT.md v1.**
