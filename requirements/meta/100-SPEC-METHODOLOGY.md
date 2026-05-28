---
document: meta/100-SPEC-METHODOLOGY
version: 3
path: requirements/meta/100-SPEC-METHODOLOGY.md
language: en
status: draft
derives-from: [D-001, D-005, D-009, D-012]
---

# Methodology — Authoring, Versioning & Lifecycle Conventions

> **Purpose of this document.** Establish the conventions that govern every requirements artifact in this repository: ID scheme, frontmatter schemas, versioning rules, tailoring syntax, artifact-to-requirement linking, git workflow, and the canonical template for detailed specs (`100`–`800`). This document is self-compliant — it follows the conventions it defines.

> **Version 3 changes.** New §13 *Test Tier Topology* codifying the six-tier test taxonomy (unit / contract / integration / coherence / e2e / system) that emerged during the v1 implementation, plus the filename conventions (`_real_chain`, `_real_llm`, `_storage_verified`) and the fixture discipline (session-scoped testcontainers + orphan wipe + cleanup-with-verify). Adds `R-M100-200..R-M100-230`. No existing entity is modified.

> **Version 2 changes.** Alignment with `D-012` (production domain extensibility): the `T-` entity type is redefined from "test specification" to the broader "validation artifact". The `@relation` marker section (§8) is generalised beyond code to cover any produced artifact type. No entity in this document has changed semantically; only the prose and the examples have been neutralised for domain independence.

---

## 1. Purpose & Scope

This methodology applies to all files under `requirements/`, including `999-SYNTHESIS.md`, the detailed specs `100-SPEC-*.md` through `800-SPEC-*.md`, and any document under `meta/`. It does not govern the produced artifacts themselves (code, documentation, presentations) — those are covered by their domain-specific specs (`600-*`, `700-*`, and future domain documents).

The methodology operationalises decisions `D-001` (StrictDoc adoption), `D-005` (tailoring rule), `D-009` (default language), and `D-012` (domain extensibility). When a rule below contradicts a synthesis decision, the synthesis prevails and a defect is filed against this document.

---

## 2. Repository Structure

```
requirements/
├── 100-SPEC-ARCHITECTURE.md
├── 200-SPEC-PIPELINE-AGENT.md
├── 300-SPEC-REQUIREMENTS-MGMT.md
├── 400-SPEC-MEMORY-RAG.md
├── 500-SPEC-UI-UX.md
├── 600-SPEC-CODE-QUALITY.md
├── 700-SPEC-VERTICAL-COHERENCE.md
├── 800-SPEC-LLM-ABSTRACTION.md
├── 999-SYNTHESIS.md
├── CHANGELOG.md
└── meta/
    └── 100-SPEC-METHODOLOGY.md  (this file)
```

**R-M100-001** *(functional, v1)* — Documents SHALL be numbered by hundreds to allow insertion of intermediate or sub-documents without renumbering. Sub-documents of an existing spec, if introduced, SHALL use suffixes (e.g. `210-SPEC-PIPELINE-AGENT-SUBAGENTS.md`).

**R-M100-002** *(functional, v1)* — The `meta/` directory SHALL contain only documents governing the methodology itself. Platform functional requirements SHALL NOT live under `meta/`.

---

## 3. Entity Model

### 3.1 Entity types

Five entity types are defined. No other types are permitted in v1.

| Prefix | Name | Scope | Lives in |
|---|---|---|---|
| `R-` | Requirement | A unit of expected behaviour, constraint, or property | `N00-SPEC-*.md` |
| `D-` | Decision | A cross-cutting architectural or methodological choice | `999-SYNTHESIS.md` only |
| `T-` | Validation artifact | An executable or verifiable acceptance criterion tied to requirements. Per production domain: a unit test for `code`, a checklist or automated content check for `documentation`, an acceptance gate for `presentation`, etc. | `N00-SPEC-*.md` appendices or dedicated validation corpora |
| `E-` | Entity / artifact | A named artifact referenced across multiple requirements (e.g. a schema, a protocol) | any spec |
| `Q-` | Open question | A deferred decision with owning spec and target resolution | any spec §"Open questions" |

**R-M100-010** *(functional, v1)* — Introducing a new entity type SHALL require an amendment to this document and approval per §9.

### 3.2 ID convention

**R-M100-020** *(functional, v1)* — Entity IDs SHALL follow the pattern `<TYPE>-<DOC-RANGE>-<SEQ>` where:
- `<TYPE>` is one of the prefixes defined in §3.1.
- `<DOC-RANGE>` is the three-digit document number (`100`, `200`, …, `999`) for root-level specs, or `M100`, `M200`, … for `meta/` documents.
- `<SEQ>` is a three-digit zero-padded sequence number, starting at `001` within each document.

**Examples**: `R-300-001`, `Q-700-042`, `T-600-017`, `R-M100-020`.

**Exception**: `D-` entities live exclusively in `999-SYNTHESIS.md` and SHALL use the flat pattern `D-NNN` (without doc-range component). Rationale: decisions are corpus-wide by construction, and the range prefix `999` would add no information.

**R-M100-021** *(functional, v1)* — Once assigned, an entity ID SHALL NOT be reused, even after deletion or deprecation.

**R-M100-022** *(functional, v1)* — When an entity is moved across documents, its ID SHALL NOT change. The original document range in the ID is preserved as historical reference, and a `supersedes:` link MAY be issued if the move implies a semantic change. Cross-document moves are expected to be rare.

### 3.3 Document-level frontmatter

**R-M100-030** *(functional, v1)* — Every file under `requirements/` SHALL begin with a YAML frontmatter block containing exactly the following fields:

```yaml
---
document: <document-slug>           # e.g. 300-SPEC-REQUIREMENTS-MGMT
version: <integer>                  # document version, see §4
path: <relative-path-from-repo-root>
language: <en|fr|...>               # ISO 639-1 code, default en
status: draft | approved | superseded
derives-from: [<D-XXX>, ...]        # optional, decisions from 999-SYNTHESIS
---
```

No other fields are permitted at document level in v1. Extensions require an amendment to this document.

### 3.4 Entity-level frontmatter

**R-M100-040** *(functional, v1)* — Every entity SHALL be introduced by a YAML block immediately preceding its prose body, containing the following fields:

```yaml
id: <TYPE-DOC-SEQ>
version: <integer>                  # entity version, see §5
status: draft | approved | superseded | deprecated
category: functional | nfr | safety | security | regulatory | ux | tooling | architecture | methodology | infrastructure | functional-scope | pipeline-design | memory-rag
```

**Optional fields**:

```yaml
derives-from: [<ID>, ...]           # upstream entities this one depends on
impacts: [<ID-pattern>, ...]        # downstream entities (wildcards allowed, e.g. R-300-*)
tailoring-of: <R-XXX>               # only at project level — see §7
override: true                      # MANDATORY if tailoring-of is set
supersedes: <ID>                    # if this entity replaces a previous one
superseded-by: <ID>                 # mirror of supersedes, set on the old entity
deprecated-reason: <string>         # required if status = deprecated
domain: <domain-name>               # for T- entities bound to a specific production domain
```

**R-M100-041** *(functional, v1)* — Fields not listed in R-M100-040 SHALL NOT appear in entity frontmatter. Parser rejects unknown fields with an error, not a warning.

**R-M100-042** *(functional, v2)* — The optional `domain:` field SHALL be present on `T-` entities when the validation artifact is specific to a single production domain (e.g. `domain: code` for a unit test). Absent `domain:` means the validation applies regardless of domain. Accepted domain values are enumerated in `999-SYNTHESIS.md` §5.12 roadmap.

---

## 4. Document Versioning Rules

**R-M100-050** *(functional, v1)* — The document-level `version:` field SHALL be a monotonically increasing integer starting at `1`, incremented by exactly `1` at every delivery (per user preferences). Gaps (e.g. 1 → 3) are not permitted.

**R-M100-051** *(functional, v1)* — The document version SHALL be incremented even if no entity changed, provided the document itself was revised (new section, reformulation of introduction, added appendix, etc.). A document version change MAY occur without any entity version change.

**R-M100-052** *(functional, v1)* — The document version SHALL NOT follow semantic versioning (no `1.2.3`). Rationale: specifications are not APIs; major/minor/patch distinctions add complexity without tangible benefit at this stage.

---

## 5. Entity Versioning Rules

This is the most operationally sensitive section of this document. Read carefully.

### 5.1 Increment triggers

**R-M100-060** *(functional, v1)* — An entity's `version:` field SHALL be incremented by exactly `1` when and only when its **semantic content** changes. Semantic content is defined as:
- The RFC 2119 keyword (SHALL, SHOULD, MAY)
- The subject and object of the requirement
- Any measurable value (threshold, duration, count, scope)
- The `category:` field
- Any field in `derives-from:`, `impacts:`, `tailoring-of:`, `override:`, `domain:`

**R-M100-061** *(functional, v1)* — Changes classified as **cosmetic** SHALL NOT trigger a version increment. Cosmetic changes include:
- Typography, whitespace, punctuation
- Reordering of adjacent sentences when meaning is preserved
- Hyperlink target normalisation
- Grammar corrections that do not alter meaning

**R-M100-062** *(functional, v1)* — When in doubt between semantic and cosmetic, the author SHALL treat the change as semantic and increment. False positives (over-incrementing) are acceptable; false negatives (missing an increment on a real change) are defects.

### 5.2 First-delivery baseline

**R-M100-063** *(functional, v1)* — A newly introduced entity SHALL start at `version: 1`. Version `0` is not permitted.

**R-M100-064** *(functional, v1)* — When an entity transitions from `draft` to `approved` without any semantic change in between, its version SHALL NOT increment. The state transition alone is tracked through `status:`.

### 5.3 History reconstruction

**R-M100-070** *(functional, v1)* — The history of an entity's versions SHALL NOT be stored inline in its frontmatter. Rationale: redundant with git, source of merge conflicts, hard to maintain correctly.

**R-M100-071** *(functional, v1)* — The platform SHALL provide a tool (`requirements-history <entity-id>`) that reconstructs version history from git log by diffing successive `version:` values. This tool is specified in `700-SPEC-VERTICAL-COHERENCE.md`.

### 5.4 Versioned references

**R-M100-080** *(functional, v1)* — A reference to an entity MAY include a version pin using the syntax `<ID>@v<N>` (e.g. `R-300-001@v3`). A reference without version pin targets the current version.

**R-M100-081** *(functional, v1)* — The vertical coherence engine SHALL detect and report drift between a versioned reference and the current version of the target entity. Reporting severity depends on the reference location (see §8 for artifact-to-requirement references).

**R-M100-082** *(functional, v1)* — Within the requirements corpus itself (entity `derives-from:`, `impacts:`, etc.), references SHALL be unversioned by default. Rationale: requirements evolve together; locking cross-references to specific versions would create false-positive drift alerts on every minor bump.

**R-M100-083** *(safety/security, v1)* — An exception to R-M100-082 applies when a requirement with `category: safety | security | regulatory` references another requirement in `derives-from:`. In that case, the reference SHALL be versioned. Rationale: regulatory traceability requires immutable cross-references.

---

## 6. Entity Lifecycle

### 6.1 States

Four states are defined. No other states are permitted in v1.

- **`draft`** — Entity is under active discussion. May be renamed, deleted, or radically reshaped without formal process.
- **`approved`** — Entity has passed review (see §9) and is part of the official corpus. Changes follow the formal amendment process.
- **`superseded`** — Entity has been replaced by another entity (linked via `superseded-by:`). Kept for historical traceability; not removed.
- **`deprecated`** — Entity is no longer valid but has no replacement. `deprecated-reason:` is mandatory. Kept for historical traceability; not removed.

### 6.2 Transitions

```
draft ──────► approved ──────► superseded
                 │
                 └────────────► deprecated
```

**R-M100-090** *(functional, v1)* — The only permitted transitions are those shown above. `superseded` and `deprecated` are terminal states.

**R-M100-091** *(functional, v1)* — An entity in `superseded` or `deprecated` state SHALL NOT be physically deleted from the document. Deletion breaks historical traceability and invalidates prior references.

**R-M100-092** *(functional, v1)* — A `draft` entity MAY be deleted without trace if it was never referenced from any `approved` entity. If it was referenced, it SHALL transition through `deprecated` instead.

---

## 7. Tailoring Convention (Platform → Project)

**R-M100-100** *(functional, v1)* — A project-level entity that refines, restricts, or overrides a platform-level parent SHALL use the following frontmatter fields:

```yaml
tailoring-of: <R-XXX>               # ID of the platform-level parent
override: true                      # mandatory, explicit acknowledgement
```

**R-M100-101** *(functional, v1)* — The `override: true` field SHALL be accompanied by a dedicated subsection `### Tailoring rationale` in the entity body, explaining why the tailoring is justified. Absence of rationale is a coherence violation.

**R-M100-102** *(functional, v1)* — Silent divergence (project-level entity that contradicts a platform parent without `tailoring-of` and `override: true`) SHALL be detected by the vertical coherence engine as check #9 (see `700-SPEC-VERTICAL-COHERENCE.md`) and reported as blocking.

**R-M100-103** *(functional, v1)* — Tailoring SHALL be limited to project-level entities targeting platform-level parents. Platform-level entities SHALL NOT tailor each other; they coexist or one supersedes the other.

**Example** (illustrative, not normative):

```yaml
id: R-500-042
version: 2
status: approved
category: ux
tailoring-of: R-500-007
override: true
```

```markdown
### Tailoring rationale

Platform-level R-500-007 requires a maximum response latency of 2 s.
This project operates in a regulated environment requiring sub-500 ms
for user-facing operations per <regulatory reference>. The stricter
bound is a refinement, not a contradiction; the platform requirement
is upheld trivially.
```

---

## 8. Artifact-to-Requirement Linking (`@relation` markers)

This section is generalised across production domains. The concrete syntax depends on the artifact format; the semantics are uniform.

**R-M100-110** *(functional, v2)* — Produced artifacts SHALL link to requirements using structured markers. The marker semantics are format-independent; the concrete syntax follows the idiomatic comment or metadata mechanism of the artifact format.

**Examples by production domain:**

For the `code` domain (Python):

```python
# @relation(R-300-001@v3, scope=function)
def parse_requirement(...):
    ...
```

For the `code` domain (Rust, Go, TypeScript): `// @relation(R-300-001@v3, scope=function)`.

For the `documentation` domain (Markdown section), v2+:

```markdown
<!-- @relation(R-500-010@v2, scope=section) -->
## User onboarding flow
...
```

For the `presentation` domain (PPTX), v3+: relations stored in slide notes or via an external sidecar (see R-M100-115).

**R-M100-111** *(functional, v1)* — The marker syntax is `@relation(<ID>@v<N>, scope=<scope>)` where:
- `<ID>` is a valid entity ID, typically `R-*` or `E-*`.
- `@v<N>` is the version pin — see R-M100-112 below.
- `<scope>` is one of `function | class | block | file | module | section | paragraph | slide | artifact`.

The `scope` vocabulary is extensible per domain; new values require an amendment.

**R-M100-112** *(functional, v1)* — Version pins in artifact-to-requirement references are governed by the three-tier rule:
- **Mandatory and blocking** for entities with `category: safety | security | regulatory`. A missing or stale version pin is a blocking finding.
- **Recommended** for entities with `category: functional | nfr`. A stale version pin is an advisory finding.
- **Optional** for entities with `category: ux | tooling | ...`. No drift reporting.

Rationale: this codifies the "option (l)" trade-off from the synthesis debates — maximum rigor where it matters, pragmatism elsewhere.

**R-M100-113** *(functional, v1)* — A single artifact block MAY carry multiple `@relation` markers if it implements or is constrained by multiple requirements. Each marker stands independently.

**R-M100-114** *(functional, v2)* — Non-Python source code artifacts SHALL use the equivalent idiomatic comment syntax for their language (`//` for Rust/Go/TS, `;` for Lisp families, etc.). Non-code artifacts (Markdown, HTML, structured data) SHALL use the commenting or metadata convention native to their format. The marker semantics are domain-agnostic.

**R-M100-115** *(functional, v1)* — When an artifact format makes inline annotations impractical (binary assets, generated artifacts, PPTX slides, image files), the relation MAY be declared in an external sidecar file `<artifact>.relations.yaml` following the schema defined in `700-SPEC-VERTICAL-COHERENCE.md`.

---

## 9. Git Workflow & Review Process

**R-M100-120** *(functional, v1)* — The `main` branch SHALL be protected. Direct commits to `main` are prohibited.

**R-M100-121** *(functional, v1)* — Every change to the requirements corpus SHALL go through a pull request. The pull request SHALL be approved by at least one reviewer distinct from its author before merge.

**R-M100-122** *(functional, v1)* — As long as the project has a single human contributor, R-M100-121 is satisfied by one of:
- an asynchronous self-review session documented in the PR description (author walks through the diff, justifies each entity change, and explicitly validates), or
- a review performed by a distinct AI agent instance configured with the `spec-reviewer-prompt` (see `200-SPEC-PIPELINE-AGENT.md`).

**R-M100-123** *(functional, v1)* — As soon as a second human contributor joins the project, R-M100-122 SHALL be void and R-M100-121 SHALL be enforced strictly with a human reviewer. This transition is automatic and requires no amendment.

**R-M100-124** *(functional, v1)* — Commits SHOULD be atomic (one logical change per commit) but this is not enforced mechanically. Rationale: atomic commits aid bisection and review but rigid enforcement friction is high; the PR as a whole is the unit of review.

**R-M100-125** *(functional, v1)* — Commit messages SHALL follow the pattern `<TYPE>: <short-description>` where `<TYPE>` is one of `feat`, `fix`, `docs`, `refactor`, `meta`. For requirements changes, `docs` is the default type.

**R-M100-126** *(functional, v1)* — A pull request affecting entities with `category: safety | security | regulatory` SHALL require explicit approval annotations of the form `Approved-by: <n>` in the merge commit trailer, for audit purposes.

---

## 10. Change Management

### 10.1 Changelog

**R-M100-130** *(functional, v1)* — A single `CHANGELOG.md` at the root of `requirements/` SHALL track all changes across the corpus, following the [Keep a Changelog](https://keepachangelog.com/) format. Per-document changelogs are prohibited (redundant with git history).

**R-M100-131** *(functional, v1)* — Every pull request SHALL update `CHANGELOG.md` under the `[Unreleased]` section. At release time, the `[Unreleased]` section is renamed to `[<release-tag>] - <date>`.

### 10.2 Supersession

**R-M100-140** *(functional, v1)* — When an entity is replaced by another, both entities SHALL carry mirrored fields: the old one has `superseded-by: <new-ID>` and `status: superseded`; the new one has `supersedes: <old-ID>`.

**R-M100-141** *(functional, v1)* — Supersession chains SHALL be linear. Fan-out supersession (one entity superseded by several) and fan-in supersession (several entities merged into one) are expressible through multiple `supersedes:` / `superseded-by:` entries but SHOULD be avoided where possible; both patterns signal that the original decomposition was unsound.

### 10.3 Deprecation

**R-M100-150** *(functional, v1)* — An entity MAY be deprecated without a replacement. The `deprecated-reason:` field SHALL explain why the entity is no longer valid and why no replacement exists.

**R-M100-151** *(functional, v1)* — Deprecated entities SHALL NOT be referenced by any entity in `approved` status. The vertical coherence engine flags dangling references to deprecated entities as blocking.

---

## 11. Detailed Spec Document Template

This section defines the canonical structure of every `N00-SPEC-*.md` document. Compliance is not optional — the vertical coherence engine validates document structure against this template.

### 11.1 Required sections, in order

```
1. Purpose & scope
2. Relationship to synthesis decisions
3. Glossary (document-specific terms only)
4. Functional requirements
5. Non-functional requirements
6. Interfaces & contracts   (omit section if not applicable)
7. Open questions
8. Appendices                (omit section if not applicable)
```

**R-M100-160** *(functional, v1)* — Sections 1, 2, 3, 4, 5, and 7 SHALL be present in every detailed spec document. Sections 6 and 8 are optional but, if present, SHALL appear in the specified order.

**R-M100-161** *(functional, v1)* — Entity IDs within a detailed spec SHALL use the document's range prefix as defined in R-M100-020.

### 11.2 Canonical skeleton

The skeleton below is the minimal conformant form of a detailed spec. Copy and adapt.

```markdown
---
document: N00-SPEC-<slug>
version: 1
path: requirements/N00-SPEC-<slug>.md
language: en
status: draft
derives-from: [D-XXX, D-YYY]
---

# <Title>

> **Purpose of this document.** <one-paragraph summary of scope and role>

## 1. Purpose & Scope

<narrative description of what this document covers and what it deliberately excludes>

## 2. Relationship to Synthesis Decisions

This document operationalises the following cross-cutting decisions from `999-SYNTHESIS.md`:

| Decision | How this document operationalises it |
|---|---|
| D-XXX | <short explanation> |
| D-YYY | <short explanation> |

## 3. Glossary

Document-specific terms only. Platform-wide terms live in the synthesis glossary.

| Term | Definition |
|---|---|
| <term> | <definition> |

## 4. Functional Requirements

### R-N00-001

\`\`\`yaml
id: R-N00-001
version: 1
status: draft
category: functional
\`\`\`

The system SHALL <testable statement>.

**Rationale.** <why this requirement exists>

### R-N00-002

...

## 5. Non-Functional Requirements

### R-N00-100

\`\`\`yaml
id: R-N00-100
version: 1
status: draft
category: nfr
\`\`\`

The system SHALL <measurable non-functional property, with threshold>.

**Rationale.** <why this threshold>

## 6. Interfaces & Contracts

<only if this document defines APIs, schemas, protocols, etc.>

### E-N00-001: <artifact name>

\`\`\`yaml
id: E-N00-001
version: 1
status: draft
category: architecture
\`\`\`

<description, schema, examples>

## 7. Open Questions

| ID | Question | Owning decision | Target resolution |
|---|---|---|---|
| Q-N00-001 | <question> | D-XXX | v1 | v2 | roadmap |

## 8. Appendices

<validation artifacts T-N00-*, diagrams, reference data, etc.>
```

### 11.3 Rules for writing requirements

**R-M100-170** *(functional, v1)* — Every requirement SHALL use RFC 2119 keywords (SHALL, SHOULD, MAY, SHALL NOT, SHOULD NOT) to express its modality. Free-form assertions without modality are rejected by the coherence engine.

**R-M100-171** *(functional, v1)* — A requirement SHALL express exactly one testable assertion. Compound requirements joined by "and" or "or" SHALL be split into separate entities.

**R-M100-172** *(functional, v1)* — Requirements with `category: nfr | safety | security | regulatory` SHALL include a `**Rationale.**` subsection explaining the origin of the constraint.

**R-M100-173** *(functional, v1)* — A requirement body SHALL be concise: one to four paragraphs. If more context is needed, the content belongs either in an appendix or in a dedicated `E-` entity.

### 11.4 Rules for writing open questions

**R-M100-180** *(functional, v1)* — Every open question SHALL be a `Q-N00-NNN` entity with at minimum: the question text, the owning decision, and a target resolution window (`v1`, `v2`, `roadmap`).

**R-M100-181** *(functional, v1)* — An open question SHALL NOT exist in `approved` status. Approval implies resolution; resolution SHALL either promote the question to a decision (new `D-` entity in the synthesis) or embed its answer in the spec and close the `Q-` entity as `superseded`.

---

## 12. Conformance & Evolution

**R-M100-190** *(functional, v1)* — This document's own compliance to the rules it defines SHALL be verified as part of the vertical coherence engine's self-check suite. Failure is a blocking CI finding.

**R-M100-191** *(functional, v1)* — Amendments to this document SHALL be treated as any other requirement change: PR, review per §9, changelog entry, version increment.

**R-M100-192** *(functional, v1)* — Conflicts between this document and any other spec SHALL be resolved in favour of the spec closer to the user need: synthesis decisions (D-XXX) prevail over this methodology; this methodology prevails over individual specs (`100`–`800`). Rationale: the hierarchy matches the intended stability gradient.

---

## 13. Test Tier Topology

> **Added in v3.** Codifies the six test tiers, the filename
> conventions, and the fixture discipline that keep each tier honest.

### 13.1 Tiers

The platform organises test suites into six tiers, listed from
fastest / lightest to slowest / heaviest. Each has a distinct purpose;
a test that fits tier N SHALL NOT be placed in a higher tier (the
slower it is, the more rare the code path it covers must be).

| Tier | Path | Dependencies | Default gate |
|---|---|---|---|
| unit | `tests/unit/<component>/` | none (no containers, no network) | blocking |
| contract | `tests/contract/<component>/` | none (schema / router / registry checks) | blocking |
| integration | `tests/integration/<component>/` | real ArangoDB + MinIO via testcontainers; other components mocked or wired in-process | blocking |
| coherence | `tests/coherence/` | none (AST + registry introspection) | blocking |
| e2e | `tests/e2e/` | testcontainers + multiple components wired in-process via `httpx.ASGITransport` | blocking |
| system | `tests/system/` | **running docker-compose stack** (opt-in via helper) | **not in default CI gate** |

**R-M100-200** *(functional, v1)* — A unit test SHALL NOT import `testcontainers`, the `arango` package, `minio`, or a running external service. An integration test SHALL use at least one real backing service (DB, object store, LLM). An e2e test SHALL drive at least two components together. A system test SHALL hit a real TCP port of a running container — not an ASGI transport.

**R-M100-201** *(functional, v1)* — A coherence test SHALL be pure-functional: it reads the source tree and/or the in-process registries. Coherence tests SHALL NOT open network sockets or spawn containers. This keeps them runnable from any CI lane, including sandboxed ones.

**R-M100-202** *(functional, v1)* — System tests SHALL be excluded from the default pytest run via `--ignore=tests/system` (opt-in) so `docker` availability is not a prerequisite for the coverage gate. Operators invoke them via `./ay_platform_core/scripts/e2e_stack.sh system` after bringing the stack up.

### 13.2 Filename conventions

**R-M100-210** *(functional, v1)* — When a single component has multiple integration styles, filenames SHALL disambiguate:

| Convention | Purpose |
|---|---|
| `test_<feature>.py` | Default: component-under-test + real infra, other components mocked or in-process. |
| `test_<feature>_real_chain.py` | Component-under-test exercises real downstream components over HTTP (via `ASGITransport`). Catches cross-component contract drift. |
| `test_<feature>_real_llm.py` | Exercises the real LLM path via an `ollama_container` fixture. Assertions are soft (outputs non-deterministic). |
| `test_<feature>_storage_verified.py` | Writes via the API, then opens raw Arango / MinIO clients to verify the storage state matches. Catches dual-store drift and silent partial failures. |

**Rationale.** Without filename signalling, a reader cannot tell at a glance whether a test is fast-and-shallow or slow-and-broad. The convention lets `pytest -k` select by intent.

### 13.3 Fixture discipline

**R-M100-220** *(functional, v1)* — Testcontainer fixtures (ArangoDB, MinIO, Ollama) SHALL be session-scoped by default. Per-test isolation SHALL be achieved at the DB / bucket level using unique UUID names, NOT by recycling the container. This keeps the full suite runnable in under a few minutes.

**R-M100-221** *(functional, v1)* — The container fixtures SHALL perform an **orphan wipe** at session start (drop every DB / bucket whose name matches the test-prefix pattern) so a prior crashed run's residue does not leak into the current one.

**R-M100-222** *(functional, v1)* — Per-test cleanup SHALL use the public helpers `cleanup_arango_database(endpoint, db_name)` and `cleanup_minio_bucket(endpoint, bucket)` defined in `tests/fixtures/containers.py`. These helpers retry and verify the drop; they SHALL NOT be wrapped in `contextlib.suppress(Exception)` (which would silence cleanup leaks). Cleanup failures are visible test failures — masking them hides bugs.

**R-M100-223** *(functional, v1)* — A function-scoped `*_fresh` variant of each container fixture SHALL remain available for tests whose isolation requirements exceed DB-level namespacing (rare; expensive — use sparingly).

### 13.4 Configuration coherence with the test tree

**R-M100-230** *(functional, v1)* — Every `.env*` file used by any tier SHALL live under `ay_platform_core/tests/`. Several variants (`.env.test`, `.env.test.integration`, …) MAY coexist; the coherence test `test_env_completeness.py` SHALL keep their key sets in lockstep with `.env.example` at the monorepo root (per R-100-110..113).

**Rationale.** Locating test env files under `tests/` keeps runtime config and test-config isolated from one another; the coherence test guarantees the isolation does not produce drift.

---

**End of meta/100-SPEC-METHODOLOGY.md v3.**
