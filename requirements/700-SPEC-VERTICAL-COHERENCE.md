---
document: 700-SPEC-VERTICAL-COHERENCE
version: 4
path: requirements/700-SPEC-VERTICAL-COHERENCE.md
language: en
status: draft
derives-from: [D-001, D-005, D-006, D-012]
---

# Vertical Coherence Specification

> **Version 4 changes (D-017).** Adds the **graded `Verdict`** envelope
> (R-700-030) that extends the binary `Finding` with a `score ∈ [0,1]`,
> `confidence`, a `method` provenance tag (`DETERMINISTIC | REFERENCE |
> JUDGED`), `rationale`, and cited `evidence` — operationalising D-017's
> three-tier evaluation harness. R-700-031 has the ValidationPlugin emit a
> graded T1 (deterministic) verdict alongside its findings. R-700-027 (#8
> `data-model-drift`) is promoted from STUB — it now compares a model's
> fields against an `E-*` `fields:`/`model_name:` declaration (T2 reference).
> R-700-032 adds the **T3 (`JUDGED`) LLM-as-judge** verdict — an opt-in,
> best-effort qualitative grade emitted alongside the T1 verdict, on a
> different model family per D-011. R-700-033 adds **quality-regression
> detection** : each run compares itself to the project's previous completed
> run and emits ADVISORY findings where quality dropped (per-entity new
> blocking, or an overall score drop) — the incremental form of the
> per-entity quality time series. R-700-022 (#3 `interface-signature-drift`)
> remains a documented STUB (deferred : it is the load-bearing deterministic
> test probe and has no `E-*` signature consumer yet — de-stub deferred to
> the session that adds the first one).
>
> **Version 3 changes.** R-700-026 (`version-drift`) and R-700-028
> (`cross-layer-coherence`) promoted from STUB to real implementations.
> R-700-022 (`interface-signature-drift`) and R-700-027
> (`data-model-drift`) remain STUBs; both depend on machine-readable
> specs on the `E-*` entities, deferred to v2.
>
> **Version 2 changes.** Scaffold populated with v1 entities for the
> Validation Pipeline Registry (C6) and the `code` production domain
> check set. Covers: plugin contract, finding model, run lifecycle,
> report persistence, 9 MUST checks (D-006). SHOULD and COULD scope
> listed without full entity coverage.

---

## 1. Purpose & Scope

This document specifies:

- The **Validation Pipeline Registry (C6)** — registry of per-domain
  validation plugins per `D-012`.
- The **plugin contract**: declaration, registration, invocation.
- The **finding model**: severity, artifact reference, location,
  fix hint.
- The **run lifecycle**: trigger → parse → evaluate → persist.
- The v1 **MUST** check set for the `code` domain (9 checks, D-006).
- **Report persistence**: ArangoDB for query access, MinIO for
  immutable snapshot.
- **`@relation` markers**: parsing rules per
  `meta/100-SPEC-METHODOLOGY.md` §8.

**Out of scope.**
- Spec storage and CRUD (→ `300-SPEC-REQUIREMENTS-MGMT.md`).
- Artifact generation (→ `200-SPEC-PIPELINE-AGENT.md`).
- `code` domain-specific quality engine (complexity, style, security
  scanners beyond vertical coherence) → `600-SPEC-CODE-QUALITY.md`
  (scaffold).
- Runtime plugin loading (v2+).

---

## 2. Entities — Registry & Contracts

#### R-700-001

```yaml
id: R-700-001
version: 1
status: approved
category: architecture
derives-from: [D-012, R-100-016]
```

C6 SHALL expose a **plugin registry** data structure indexed by
production domain. Each registered plugin SHALL declare:

- `domain: str` — the production domain identifier (`code`, …).
- `name: str` — unique plugin identifier.
- `version: str` — semver plugin version.
- `checks: list[CheckSpec]` — the checks this plugin implements.
- `artifact_formats: list[str]` — MIME types or file-extension globs
  the plugin parses (`python`, `markdown`).

A plugin registered for an already-registered `(domain, name)` pair
SHALL raise `PluginAlreadyRegisteredError` at registration time.

#### R-700-002

```yaml
id: R-700-002
version: 1
status: approved
category: architecture
derives-from: [R-100-016]
```

At C6 startup, plugins SHALL be discovered and registered via **build-
time Python import** of each plugin module. Runtime hot-reload is
deferred to v2.

#### R-700-003

```yaml
id: R-700-003
version: 1
status: approved
category: architecture
```

Each plugin SHALL implement a `ValidationPlugin` Python Protocol with
the following async method:

```python
async def run_check(
    self,
    check_id: str,
    context: CheckContext,
) -> CheckResult: ...
```

`CheckContext` contains: `project_id`, `requirements: list[EntityPublic]`,
`artifacts: list[CodeArtifact]`, `relation_markers: list[RelationMarker]`.

`CheckResult` contains: `findings: list[Finding]` and an aggregated
status (`passed`, `failed`, `error`).

---

## 3. Entities — Finding Model

#### E-700-001

```yaml
id: E-700-001
version: 1
status: approved
category: architecture
```

**Finding** — one validation result row. Fields:

| Field | Type | Description |
|---|---|---|
| `finding_id` | `str` (UUID) | Unique identifier; populated by the service. |
| `run_id` | `str` | Parent run reference. |
| `check_id` | `str` | Identifier of the check that produced the finding (e.g. `req-without-code`). |
| `domain` | `str` | Production domain of the plugin. |
| `severity` | enum | `blocking`, `advisory`, `info`. |
| `status` | enum | `open`, `resolved`, `suppressed`. |
| `artifact_ref` | `str \| None` | Path or URI of the artifact in scope (e.g. `src/foo.py`). |
| `location` | `str \| None` | File:line or line range (`src/foo.py:42` or `42-57`). |
| `entity_id` | `str \| None` | Related entity, if applicable (e.g. `R-300-100`). |
| `message` | `str` | Human-readable description. |
| `fix_hint` | `str \| None` | Optional remediation guidance. |
| `created_at` | datetime | Server time at creation. |

#### E-700-002

```yaml
id: E-700-002
version: 1
status: approved
category: architecture
```

**ValidationRun** — one execution of one or more checks. Fields:

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` (UUID) | Unique identifier. |
| `project_id` | `str` | Target project. |
| `domain` | `str` | Production domain. |
| `check_ids` | `list[str]` | Checks requested (empty ⇒ all). |
| `status` | enum | `pending`, `running`, `completed`, `failed`. |
| `findings_count` | `{blocking,advisory,info}` | Summary counts. |
| `started_at`, `completed_at` | datetime | Wall-clock timing. |
| `snapshot_uri` | `str \| None` | MinIO URI of the immutable report snapshot (populated on `completed`). |

---

## 4. Entities — Run Lifecycle

#### R-700-010

```yaml
id: R-700-010
version: 1
status: approved
category: functional
```

A validation run SHALL be **triggered** via `POST /validation/runs` with
body `{domain, project_id, check_ids?}`. If `check_ids` is omitted, all
checks of the target domain SHALL run.

#### R-700-011

```yaml
id: R-700-011
version: 1
status: approved
category: functional
```

Runs SHALL execute **in-process** in v1 via `asyncio.create_task`. The
endpoint SHALL return `202 Accepted` with the `run_id` without awaiting
completion. Migration to NATS-worker queueing is deferred to v2.

#### R-700-012

```yaml
id: R-700-012
version: 1
status: approved
category: functional
```

Upon completion, findings SHALL be persisted to ArangoDB collection
`c6_findings` (document keyed by `finding_id`). The run row in
`c6_runs` SHALL be updated with `status=completed` and `findings_count`.

#### R-700-013

```yaml
id: R-700-013
version: 1
status: approved
category: functional
```

Upon completion, an **immutable JSON snapshot** of the run (run metadata
+ all findings) SHALL be written to MinIO at
`validation-reports/<project_id>/<run_id>.json`. The `snapshot_uri`
on the run row SHALL reference this path.

#### R-700-014

```yaml
id: R-700-014
version: 1
status: approved
category: functional
```

If a plugin raises during `run_check()`, the run SHALL NOT fail
globally. Instead, C6 SHALL emit a `severity=info` finding of
`check_id="<check>:error"` with the exception message and continue
with remaining checks. Run status transitions to `completed` unless
every check errored, in which case `failed`.

---

## 5. Entities — MUST Check Set (D-006, v1 `code` domain)

Each check below is a `R-700-0NN` entity and is bound to the built-in
`code` plugin. Severity for MUST checks is `blocking` by default.

#### R-700-020

```yaml
id: R-700-020
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #1 — `req-without-code`.** Any requirement with
`status == approved` AND `type in {R, E}` SHALL have at least one
code artifact containing an `@relation implements:<req_id>` marker.
Missing marker ⇒ finding `blocking`.

#### R-700-021

```yaml
id: R-700-021
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #2 — `code-without-requirement`.** Any non-test Python module
under `src/` SHALL reference at least one entity via `@relation`. Dead
modules (0 markers) ⇒ finding `blocking`. Scope SHALL exclude
`__init__.py`, modules under `tests/`, and modules tagged
`# @relation ignore-module`.

#### R-700-022

```yaml
id: R-700-022
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #3 — `interface-signature-drift`** (v1: STUB). Scope: Pydantic
model field sets and function signatures referenced by `@relation
implements:E-*`. v1 emits `severity=info` stub findings; real
implementation lands in v2 when the `E-` entities carry
machine-readable signature specs.

#### R-700-023

```yaml
id: R-700-023
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #4 — `test-absent-for-requirement`.** Any requirement with
`status == approved` SHALL be referenced by at least one artifact under
`tests/` via `@relation validates:<req_id>` or `implements:<req_id>`.
Missing ⇒ `blocking`.

#### R-700-024

```yaml
id: R-700-024
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #5 — `orphan-test`.** Any file under `tests/` SHALL reference
at least one entity via `@relation validates:` or `implements:`.
v1 excludes fixtures (`tests/fixtures/`), conftest, and files tagged
`# @relation ignore-test-file`. Unreferenced ⇒ `blocking`.

#### R-700-025

```yaml
id: R-700-025
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #6 — `obsolete-reference`.** For every `@relation` marker
targeting an entity id, the referenced entity SHALL exist in C5. If
the entity is `status == deprecated` or not found, the marker is a
`blocking` finding.

#### R-700-026

```yaml
id: R-700-026
version: 2
status: approved
category: functional
derives-from: [D-006]
```

**Check #7 — `version-drift`.** For every version-pinned marker
`@relation <verb>:<entity>@v<K>`, the current entity version in C5
SHALL equal K. Any mismatch (stale pin, missing entity) SHALL produce a
`blocking` finding. Un-pinned markers are out of scope for this check
(covered by `obsolete-reference` when the target is missing or
deprecated).

#### R-700-027

```yaml
id: R-700-027
version: 1
status: approved
category: functional
derives-from: [D-006]
```

**Check #8 — `data-model-drift`.** An `E-*` entity that defines a data
contract MAY declare a machine-readable canonical field set via
`fields: [<name>, …]` plus the implementing class via
`model_name: <ClassName>`. For each such entity, the check locates the
Pydantic class `model_name` in the run's code artifacts and compares its
declared field names to `fields:` — a **missing** field (in the spec, absent
from the model) or an **extra** field (in the model, absent from the spec)
produces a `blocking` `data-model-drift` finding. `E-*` entities without a
`fields:`/`model_name:` declaration are SKIPPED (the mechanism is opt-in, so
unannotated entities never false-positive). Inherited fields are out of scope
in v1 (direct annotations only). (Promoted from STUB in v4.)

#### R-700-028

```yaml
id: R-700-028
version: 2
status: approved
category: functional
derives-from: [D-006, D-005]
```

**Check #9 — `cross-layer-coherence`.** Every project-level entity that
declares `tailoring-of: <parent>` SHALL also set `override: true`. C5
enforces this at write time (R-M100-070); C6 provides defence-in-depth
for corpora imported outside the normal write path. Missing
`override: true` SHALL produce a `blocking` finding.

---

## 5bis. Graded Verdicts (D-017)

#### R-700-030

```yaml
id: R-700-030
version: 1
status: draft
category: functional
derives-from: [D-017]
```

The validation layer SHALL emit, alongside the binary `Finding`, a graded
**`Verdict`** : `score ∈ [0.0, 1.0]`, `confidence ∈ [0.0, 1.0]`, a `method`
provenance tag in `{DETERMINISTIC, REFERENCE, JUDGED}` (the evaluation tier
T1/T2/T3 that produced it), a free-text `rationale`, and a list of cited
`evidence` (finding ids / artifact refs the score is grounded in). A
`Verdict` makes the epistemic status of a measurement explicit — a
deterministic coverage score (`DETERMINISTIC`, confidence 1.0) is not the
same object as an LLM-judged clarity score (`JUDGED`). Golden datasets used
by `REFERENCE` verdicts SHALL be held-out and SHALL NOT be tuned against
(anti "teaching-to-the-test").

**Rationale.** Binary pass/fail loses signal a regulated platform needs to
present as quality evidence (ISO 21434 / ASPICE). A provenance-tagged graded
verdict turns the check backlog into a measured, prioritised one and is the
substrate for the per-entity quality time series (regression detection).

#### R-700-031

```yaml
id: R-700-031
version: 1
status: draft
category: functional
derives-from: [D-017]
```

Each validation run SHALL produce at least one **T1 (`DETERMINISTIC`)**
verdict derived from its deterministic findings — a reference-free grade of
the run's coherence (e.g. severity-weighted), with the contributing findings
cited as `evidence` and `confidence = 1.0`. The grading SHALL be a pure
function of the findings (no LLM, no I/O), so it is reproducible. The T2
(`REFERENCE`, e.g. the `E-*` drift check R-700-027) tier extends this same
`Verdict` envelope ; T3 (`JUDGED`) is specified in R-700-032.

#### R-700-032

```yaml
id: R-700-032
version: 1
status: draft
category: functional
derives-from: [D-017, D-011]
```

A validation run MAY additionally emit a **T3 (`JUDGED`)** verdict produced
by an LLM-as-judge that grades a qualitative dimension the deterministic (T1)
and reference (T2) tiers cannot capture — e.g. clarity, completeness, and
apparent requirement-satisfaction of the run's artifacts. The judge SHALL be
prompted with the run's requirements and code artifacts and SHALL return a
structured grade mapped onto the `Verdict` envelope (R-700-030) with
`method = JUDGED`, a `confidence < 1.0` (an LLM grade is never certain), a
`rationale`, and `evidence` (artifact refs / entity ids the score is grounded
in).

Per **D-011** (model diversity), the judge SHALL run on a **different model
family** than the one that produced the artifacts (anti self-preference
bias) ; this is enforced operationally by routing the judge through a
dedicated C8 agent route (`c6-judge`) configured to a non-generator family.
The judged grade is **opt-in** (off by default ; gated by `C6_JUDGE_ENABLED`)
and **best-effort** : a judge failure (provider error, unparseable response)
SHALL NOT fail the run nor suppress its T1 verdict — the run completes with
`judged_verdict = null`. The held-out / anti "teaching-to-the-test"
discipline of R-700-030 applies to any rubric or reference the judge cites.

#### R-700-033

```yaml
id: R-700-033
version: 1
status: draft
category: functional
derives-from: [D-017]
```

The validation layer SHALL detect **quality regressions** by comparing each
completed run to the project's **previous completed run** (same domain). The
per-entity quality "time series" is realised **incrementally** : rather than
persisting a separate series, every run grades itself against its predecessor
and emits an **ADVISORY** finding (`check_id = quality-regression`) for each
regression, so the signal surfaces exactly where it is read — in the run's own
findings. Two regressions are reported:

- **Per-entity** : an entity that **gained** blocking findings since the
  previous run (was clean / advisory before, blocking now) ⇒ one ADVISORY
  finding citing that `entity_id`.
- **Overall** : the run's deterministic (T1) verdict `score` is **lower** than
  the previous run's ⇒ one ADVISORY finding citing the score delta.

The regression findings SHALL be appended **after** the T1 verdict is graded,
so they never lower the score the next run compares against (anti-feedback).
The comparison is **best-effort** on the read : the first run of a project (no
predecessor) and any history-read error yield no regression findings and SHALL
NOT fail the run. An improvement or a steady state never produces a finding.

---

## 6. `@relation` Marker Parsing

#### R-700-040

```yaml
id: R-700-040
version: 1
status: approved
category: tooling
```

A marker has syntax `@relation <verb>:<target>[,<target>]*`. Verbs
accepted by v1: `implements`, `validates`, `uses`, `derives-from`.
Targets are entity ids (optionally version-pinned with `@vN`), or the
sentinel `ignore-module` / `ignore-test-file` (no target). Markers
SHALL appear in Python comments (`#`) or docstrings.

#### R-700-041

```yaml
id: R-700-041
version: 1
status: approved
category: tooling
```

The parser SHALL collect one `RelationMarker(path, line, verb, targets)`
per marker encountered. Malformed markers (unknown verb, invalid entity
id) SHALL generate a separate `check_id=marker-syntax` finding at
`severity=blocking`.

---

## 7. Configuration

#### R-700-050

```yaml
id: R-700-050
version: 1
status: approved
category: tooling
```

C6 SHALL support per-check enable/disable through config (env prefix
`C6_CHECK_<CHECK_ID_UPPERCASED>_ENABLED`, default `true`). Disabled
checks are skipped at run time with a single `severity=info` finding
`check_id=<check>:disabled`.

---

## 8. Roster of REST Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/validation/plugins` | Registered plugins, domains, checks. |
| GET | `/validation/domains` | Declared production domains (v1: `code`). |
| POST | `/validation/runs` | Trigger a run. Returns `202` with `run_id`. |
| GET | `/validation/runs/{run_id}` | Run metadata + summary counts. |
| GET | `/validation/runs/{run_id}/findings` | Paginated findings. |
| GET | `/validation/findings/{finding_id}` | Single finding. |

---

## 9. Open Questions

#### Q-700-001

```yaml
id: Q-700-001
version: 1
status: draft
category: functional
```

SHOULD (v2): integration with `coverage.py` for real execution coverage
(check #10). Mechanism TBD (coverage XML ingest vs coverage plugin).

#### Q-700-002

```yaml
id: Q-700-002
version: 1
status: draft
category: functional
```

How SHOULD per-project check exclusions be declared? Config-side
(`C6_CHECK_*`) is global. Per-project YAML in the project's
requirements bucket is likely the v2 answer.

---

*End of 700-SPEC-VERTICAL-COHERENCE v2.*
