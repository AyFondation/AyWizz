---
document: 310-SPEC-DOC-TRACEABILITY
version: 3
path: requirements/310-SPEC-DOC-TRACEABILITY.md
language: en
status: draft
derives-from: [D-005, D-012, D-023, D-024, D-025, D-026, D-027]
---

# Document Traceability & Engineering Cycle Specification

> **Purpose of this document.** Specifies the cascade of engineering
> documents that answer externally-supplied requirements, with
> object-level traceability, LLM-proposed / human-reviewed production,
> change absorption through an impact DAG, and baseline export. It is a
> sub-document of `300-SPEC-REQUIREMENTS-MGMT.md` (per `R-M100-001`
> suffix insertion) and extends the Requirements Service corpus model
> from documents-of-entities to documents-of-objects.

---

## 1. Purpose & Scope

**In scope.**

- The **engineering cycle** (`C-`) — a versioned, publishable model of
  document types, the links permitted between their layers, and each
  container's scope.
- The **workflow** (`WF-`) — a versioned, publishable activity
  definition with typed steps and machine-checkable acceptance
  criteria, consumed by agents and verified by C6.
- The **object model** — documents as containers of addressable,
  individually reviewable objects, with a coverage graph linking them
  to requirements and to each other.
- **Intake of externally-supplied requirements** (customer, standard,
  regulation), their quality review, their splitting into fragments,
  and their allocation to cycle containers.
- **Change absorption** — deterministic impact traversal, systematic
  per-node review, and a closure gate.
- **Conversational piloting** — the negotiated treatment plan by which
  an agent proposes work and a human arbitrates its granularity.
- **Retention, baselines, and rendering** to DOCX / PDF.

**Out of scope.**

- The rendering of the workbench shell itself (panel geometry,
  keyboard bindings, tab behaviour). Those are UI concerns owned by
  `500-SPEC-UI-UX.md`; this document states only the *functional* UI
  requirements the activity imposes (§4.12), and an amendment to
  `500-SPEC` is required to realise them.
- Requirement entity CRUD, versioning, history, tailoring and reindex
  mechanics, which remain owned by `300-SPEC-REQUIREMENTS-MGMT.md`.
- Agent routing and cost control, owned by
  `800-SPEC-LLM-ABSTRACTION.md`.
- The `code` production domain pipeline, owned by
  `200-SPEC-PIPELINE-AGENT.md`.

**Relationship to 300-SPEC.** This document does not supersede
`300-SPEC`. It adds a finer storage grain (the object) beneath the
document, and three new corpus-resident entity kinds (`C-`, `WF-`,
change tickets). `R-100-020` (MinIO is the source of truth) and
`R-300-013` (the ArangoDB index is rebuildable) are preserved
unchanged and are load-bearing for §4.1.

---

## 2. Relationship to Synthesis Decisions

This document operationalises the following cross-cutting decisions
from `999-SYNTHESIS.md`:

| Decision | How this document operationalises it |
|---|---|
| D-005 | The platform → project requirement hierarchy is extended to the cycle and workflow catalogues: both are published at tenant level and tailored per project, reusing the `tailoring-of` / `override` convention of `meta/100-SPEC-METHODOLOGY.md` §7. |
| D-012 | The object, coverage and cycle models carry no domain vocabulary. A cycle instance names its layers; the backbone stores them as opaque container types. Document-engineering is a *second production domain* expressed as cycle + workflow data, not as new backbone code paths. |
| D-023 | §4.1 realises the object-grain model: one JSON object per file in MinIO, a rebuildable Arango graph, renderings never authoritative, and decision-triggered versioning with full uncompacted retention (§4.11). |
| D-024 | §4.2 and §4.3 realise the cycle (`C-`) and workflow (`WF-`) entities: tenant-published, project-tailorable, immutable once published, typed steps only, non-empty `checks` as a publication precondition. |
| D-025 | §4.4 and §4.5 realise multi-container allocation, interval-anchored non-destructive splitting, and cluster review with an `auto-accepted` state distinct from `accepted`. |
| D-026 | §4.8 realises change absorption: deterministic traversal, DAG semantics, system-managed tickets, and disposition-based closure. |
| D-027 | §4.10 realises lease-based object locking held by human and agent actors alike, backed by the optimistic version check of `R-300-060`. |

---

## 3. Glossary

Document-specific terms only. Platform-wide terms live in the
synthesis glossary.

| Term | Definition |
|---|---|
| **Object** | The smallest addressable, individually reviewable unit of a document: a heading, a paragraph, a list, a table, or a figure. Carries its own identity, version, and review state. |
| **Container** | A document type declared by a cycle (e.g. *Functional description*), holding objects and carrying a scope statement. |
| **Cycle** (`C-`) | The versioned graph of containers and the link kinds permitted between them; the project's engineering process expressed as data. |
| **Workflow** (`WF-`) | A versioned activity definition: typed steps, contract, and machine-checkable acceptance criteria. |
| **Coverage link** | An edge asserting that an object answers a requirement or an upstream object (`covers`). |
| **Allocation** | An edge asserting that a requirement must be answered by a given container (`allocated-to`). A requirement may carry several. |
| **Fragment** | A sub-requirement derived from a supplied requirement by splitting, anchored on a text interval of the untranslated source. |
| **Suspect link** | A coverage link whose pinned target version is behind the target's current version. |
| **Impact DAG** | The set of nodes reachable from a changed requirement through coverage and derivation edges. A directed acyclic graph, not a tree: multi-allocation makes branches converge. |
| **Treatment plan** | An agent-proposed, human-ratified description of how a batch of requirements or changes will be processed, including effort classification and cost estimate. |
| **Baseline** | A named, immutable photograph of the corpus: a tag plus a manifest of object versions, link pins, and content hashes. |
| **Cluster** | A group of supplied requirements sharing a theme and a proposed allocation, reviewed as one unit. |

---

## 4. Functional Requirements

### 4.1 Object model & storage

#### R-310-001

```yaml
id: R-310-001
version: 1
status: draft
category: architecture
derives-from: [R-100-020, R-300-013]
```

Each object SHALL be persisted as one JSON document in MinIO under
`projects/<project-id>/docs/<container-slug>/objects/<object-id>.json`,
and MinIO SHALL remain the sole source of truth for object content.

**Rationale.** Object-grain writes are required because an agent
reworks one paragraph, not a document (`R-310-124`), and because two
actors must be able to work on one container concurrently. A single
Markdown blob per document would force a read-modify-write of the whole
container on every edit. Persisting objects in MinIO rather than in
ArangoDB preserves `R-100-020` without loss of any property.

#### R-310-002

```yaml
id: R-310-002
version: 1
status: draft
category: architecture
derives-from: [R-300-013]
impacts: [R-310-141]
```

The ArangoDB object and relation collections SHALL be a derived index,
entirely rebuildable from MinIO by the reindex operation of
`300-SPEC` §4.7, and SHALL NOT hold any state absent from MinIO.

**Rationale.** The graph exists for traversal performance (§4.8) and
matrix rendering (§4.12), both of which are read paths. Making it
authoritative would invert `R-100-020`, a platform invariant.

#### R-310-003

```yaml
id: R-310-003
version: 1
status: draft
category: architecture
```

Markdown, DOCX, PDF and SVG artefacts SHALL be renderings produced
from objects on demand, and SHALL NOT be authoritative for any object
content or traceability link.

#### R-310-004

```yaml
id: R-310-004
version: 1
status: draft
category: functional
```

An object's `type` SHALL be one of `heading`, `paragraph`, `list`,
`table`, or `figure`. Introducing a further type SHALL require an
amendment to this document.

#### R-310-005

```yaml
id: R-310-005
version: 1
status: draft
category: functional
```

An object identifier SHALL be stable across every subsequent edit of
that object's content, and SHALL NOT be reused after the object is
deleted.

**Rationale.** Every coverage link, review decision and impact
traversal addresses objects by identifier. An identifier that shifts
when text is rewritten silently detaches the traceability graph from
the content it describes.

#### R-310-006

```yaml
id: R-310-006
version: 1
status: draft
category: functional
```

Every object and every coverage link SHALL carry a review state drawn
from `proposed`, `accepted`, `auto-accepted`, `stale`, or `rejected`.

#### R-310-007

```yaml
id: R-310-007
version: 1
status: draft
category: functional
impacts: [R-310-070, R-310-225]
```

The `auto-accepted` state SHALL be distinct from `accepted` in storage,
in every API response, and in every displayed coverage figure.

**Rationale.** Cluster review (`R-310-070`) is the only tractable way
to review tens of thousands of requirements, but it grants acceptance
without individual human examination. Collapsing it into `accepted`
would make a coverage figure that cannot be interpreted, and a figure
that cannot be interpreted is worse than no figure.

#### R-310-008

```yaml
id: R-310-008
version: 1
status: draft
category: functional
derives-from: [R-310-003]
```

A `figure` object's authoritative content SHALL be a textual
description in a standardised notation, from which the rendered SVG is
produced.

**Rationale.** A textual source is diffable, versionable and editable
by an agent under the same review mechanics as prose. A binary image
would be the only object in a document outside the traceability and
review model.

#### R-310-009

```yaml
id: R-310-009
version: 1
status: draft
category: functional
impacts: [R-310-202, R-310-206]
```

An object SHALL hold at most one **working draft**, overwritten by each
iteration of a negotiation, and writing a working draft SHALL NOT
create an object version.

**Rationale.** Converging on an acceptable rework takes several
exchanges between a reviewer and an agent. Versioning each exchange
fills the history with machine attempts that carry no decision, which
makes the history unusable for the audit it exists to serve.

#### R-310-010

```yaml
id: R-310-010
version: 1
status: draft
category: regulatory
derives-from: [R-310-009]
```

A new object version SHALL be created only when a review decision
accepts the working draft or confirms the object requires no change,
and every object version SHALL therefore carry an actor, a timestamp
and a review decision.

**Rationale.** This makes the version chain a history of decisions
rather than of attempts. Every entry is then, by construction,
attributable evidence — which is exactly what `R-310-305` requires and
what an auditor reconstructs.

#### R-310-011

```yaml
id: R-310-011
version: 1
status: draft
category: functional
derives-from: [R-310-009, R-310-010]
```

The iterations of a negotiation SHALL be retained in the run record of
the conversation that produced them, and SHALL NOT be reconstructible
from the object version chain.

**Rationale.** Dropping intermediate versions loses the trace of how an
agent arrived at a result. That trace still has diagnostic value, so it
is kept where it is cheap and where it belongs — the conversation
ledger — rather than inflating the corpus with it.

### 4.2 The engineering cycle

#### R-310-020

```yaml
id: R-310-020
version: 1
status: draft
category: functional
```

The platform SHALL represent the engineering cycle as a `C-` entity
carrying a monotonic version and a `status` of `draft`, `approved` or
`superseded`.

#### R-310-021

```yaml
id: R-310-021
version: 1
status: draft
category: functional
impacts: [R-310-066]
```

A cycle SHALL declare, for each container: its slug, its ordinal
position, its permitted outgoing and incoming link kinds, whether
coverage of allocated requirements is obligatory, and a **scope
statement** identified by a `scope_id`.

**Rationale.** The scope statement is not documentation. It is the
input an agent uses to allocate (`R-310-066`) and the reference a
document owner cites when returning an allocation (`R-310-068`). Its
quality determines allocation quality, which makes it measurable
(`R-310-069`).

#### R-310-022

```yaml
id: R-310-022
version: 1
status: draft
category: functional
derives-from: [D-005]
```

Cycles SHALL be published at tenant scope and MAY be tailored per
project using the `tailoring-of` / `override` convention of
`meta/100-SPEC-METHODOLOGY.md` §7, with a mandatory tailoring
rationale.

#### R-310-023

```yaml
id: R-310-023
version: 1
status: draft
category: functional
impacts: [R-310-045]
```

A published version of a cycle SHALL be immutable; editing a published
cycle SHALL create a new `draft` version and SHALL NOT mutate the
published one.

**Rationale.** Objects record the cycle version under which they were
produced. Mutating a published version retroactively changes the
meaning of that record and destroys the audit trail.

#### R-310-024

```yaml
id: R-310-024
version: 1
status: draft
category: regulatory
derives-from: [R-310-023]
```

A project SHALL be bound to exactly one cycle version per baseline,
and moving a project to a different cycle version SHALL require an
explicit migration operation that produces a report enumerating every
affected container, object and link.

**Rationale.** A cycle change alters the shape of the traceability
graph — a removed layer orphans every object it held. A silent
re-shape would leave a regulated corpus in a state no auditor can
reconstruct. The migration report is the evidence that the change was
deliberate and bounded.

#### R-310-025

```yaml
id: R-310-025
version: 1
status: draft
category: functional
```

An activity on a container SHALL NOT start unless a published workflow
is bound to that container's phase in the active cycle version.

**Rationale.** Without this precondition, "workflows are applied
systematically" degrades to "workflows are usually applied", and the
capitalisation the mechanism exists to provide never accrues.

### 4.3 Workflows

#### R-310-040

```yaml
id: R-310-040
version: 1
status: draft
category: functional
```

The platform SHALL represent each activity as a `WF-` entity carrying
a monotonic version and a `status` of `draft`, `approved` or
`superseded`.

#### R-310-041

```yaml
id: R-310-041
version: 1
status: draft
category: functional
impacts: [E-310-003]
```

A workflow SHALL declare `intent`, `inputs`, `steps`, `constraints`,
`outputs`, `checks` and `examples`, and a workflow whose `checks` list
is empty SHALL NOT be publishable.

**Rationale.** A workflow without machine-checkable acceptance
criteria is a named prompt. It cannot be verified, so it cannot be
capitalised on, and it degrades silently as the corpus evolves.

#### R-310-042

```yaml
id: R-310-042
version: 1
status: draft
category: functional
```

Every workflow step SHALL declare a kind of `agent`, `check`, or
`human-gate`.

#### R-310-043

```yaml
id: R-310-043
version: 1
status: draft
category: functional
derives-from: [R-310-042]
```

A workflow SHALL express control flow only as an ordered sequence of
steps plus, on a `human-gate` step, a return target naming an earlier
step; conditionals, loops, variables and expressions SHALL NOT be
expressible in a workflow definition.

**Rationale.** Conditional workflow languages become general-purpose
schedulers that nobody authors and nobody can verify. Conditionality
belongs in the phase gates of the cycle, where it is visible.

#### R-310-044

```yaml
id: R-310-044
version: 1
status: draft
category: functional
```

The prompt supplied to an agent SHALL be rendered from the workflow's
declared fields, and the authoring surface SHALL NOT expose a free-form
prompt body as an editable field.

#### R-310-045

```yaml
id: R-310-045
version: 1
status: draft
category: functional
derives-from: [R-310-023]
```

A published workflow version SHALL be immutable, and every object
SHALL record the workflow identifier and version that produced it.

**Rationale.** This record is what makes *"the method changed; here are
the 340 objects produced under the previous version"* answerable, which
is the concrete form of capitalisation. It is worthless if a published
version can be edited in place.

#### R-310-046

```yaml
id: R-310-046
version: 1
status: draft
category: functional
derives-from: [D-005, R-310-022]
```

The platform SHALL ship a standard workflow catalogue at tenant scope,
and a project SHALL be able to tailor it — disable, restrict, or
override a workflow — with a mandatory tailoring rationale.

#### R-310-047

```yaml
id: R-310-047
version: 1
status: draft
category: functional
```

The platform SHALL expose an authoring surface allowing an authorised
user to create, edit, version and publish `C-` and `WF-` entities
without filesystem or API access.

### 4.4 Intake, quality review and allocation

#### R-310-060

```yaml
id: R-310-060
version: 1
status: draft
category: functional
impacts: [R-300-080]
```

Intake SHALL accept ReqIF, XLSX, DOCX and PDF sources, and SHALL
classify each source into a confidence class of `structured`
(ReqIF, XLSX), `textual` (DOCX, PDF with a text layer), or `degraded`
(PDF requiring OCR).

**Rationale.** `R-300-080` currently restricts v1 import to Markdown
and ReqIF; it requires amendment. The confidence class is not
cosmetic: it gates `R-310-061` and determines whether interval anchors
(`R-310-091`) can be trusted.

#### R-310-061

```yaml
id: R-310-061
version: 1
status: draft
category: functional
derives-from: [R-310-060]
```

A source of class `degraded` SHALL require human verification of the
extracted text before any splitting or allocation is proposed on it.

**Rationale.** Interval anchors are character offsets into the source
text. OCR misrecognition shifts those offsets, so the exhaustiveness
check of `R-310-092` would pass against corrupted text and lose a
clause invisibly.

#### R-310-062

```yaml
id: R-310-062
version: 1
status: draft
category: functional
```

Every requirement created by intake SHALL carry a resolvable source
anchor naming the source file, its location within that file, and the
text interval it was extracted from.

#### R-310-063

```yaml
id: R-310-063
version: 1
status: draft
category: functional
```

Every negative finding of the quality review SHALL cite the
identifier of a criterion declared in the workflow's `checks`, and a
finding without such an identifier SHALL NOT be surfaced to a
reviewer.

**Rationale.** An LLM asked to review requirements will produce
plausible objections without limit. Anchoring each finding to a
declared criterion makes false positives cheap to dismiss and makes the
criterion set itself reviewable and versionable.

#### R-310-064

```yaml
id: R-310-064
version: 1
status: draft
category: functional
impacts: [R-310-096, R-310-143]
```

A requirement SHALL be allocatable to one or more containers, and its
coverage SHALL be satisfied only when every one of its allocations is
covered.

**Rationale.** A single-container model cannot express an ASIL-rated
requirement allocated simultaneously to the architecture design and
the security analysis. Single allocation is the degenerate case of
multiple allocation, so the general model costs nothing in
expressiveness. The consequence is accepted: a coverage gap may be
partial.

#### R-310-065

```yaml
id: R-310-065
version: 1
status: draft
category: functional
```

No accepted requirement SHALL be left without either at least one
allocation or an explicit `out-of-project` verdict carrying a
justification.

**Rationale.** The symmetric failure of an allocating agent is not a
wrong target but silent omission of the requirements it cannot place.
Requiring an explicit verdict converts that silence into a reviewable
statement.

#### R-310-066

```yaml
id: R-310-066
version: 1
status: draft
category: functional
derives-from: [R-310-021]
```

Every proposed allocation SHALL cite the `scope_id` of the container
scope statement that justifies it, and an allocation citing no
existing `scope_id` SHALL be rejected before reaching a reviewer.

**Rationale.** Without this constraint an agent allocates by lexical
similarity — a requirement mentioning a bus protocol is sent to the
technical design although it is an architecture or a security
requirement. Requiring a scope citation forces the decision to be made
against the declared process.

#### R-310-067

```yaml
id: R-310-067
version: 1
status: draft
category: safety
derives-from: [R-310-064]
```

Criticality carried by a requirement SHALL propagate to every
container it is allocated to, unless an explicit, justified
decomposition is recorded against that allocation.

**Rationale.** Uniform propagation is the safe default. The
decomposition exception exists because safety standards permit
allocating a lower integrity level to decomposed elements; a tool that
treated every decomposition as a violation would contradict the
standard and be worked around.

#### R-310-068

```yaml
id: R-310-068
version: 1
status: draft
category: functional
```

The owner of a container SHALL be able to return a requirement
allocated to it, and the return SHALL be recorded as a persistent
`allocation-rejected` edge carrying a reason code of `out-of-scope`,
`not-atomic`, or `out-of-project`, the actor, and the timestamp.

**Rationale.** Recording rather than deleting is what makes the replay
better-informed: an erased allocation leaves the agent no trace of the
failure, so it proposes the same target again. The reason code routes
the outcome — `out-of-scope` to re-allocation, `not-atomic` to
splitting, `out-of-project` back to the issuer.

#### R-310-069

```yaml
id: R-310-069
version: 1
status: draft
category: functional
derives-from: [R-310-068]
```

Re-allocation SHALL exclude every previously returned container unless
a human explicitly overrides the exclusion, and the second return of a
given requirement SHALL escalate to a human arbitration gate rather
than trigger a further automated re-allocation.

**Rationale.** Two container owners can return the same requirement to
each other indefinitely. Without a terminator the loop is unbounded
and consumes budget at every turn.

#### R-310-070

```yaml
id: R-310-070
version: 1
status: draft
category: functional
impacts: [R-310-007]
```

Intake review SHALL support grouping requirements into clusters
sharing a theme and a proposed allocation, and accepting a cluster
SHALL set every requirement it contains to `auto-accepted`.

**Rationale.** At the target volume of `R-310-300`, exhaustive
individual review of intake is not physically achievable; the
predictable outcome of demanding it is mass acceptance, which
eliminates supervision altogether. Cluster review is tractable and,
because `auto-accepted` is a distinct state, it remains honest and
revisitable.

#### R-310-071

```yaml
id: R-310-071
version: 1
status: draft
category: functional
derives-from: [R-310-070]
```

Individual human review SHALL remain mandatory, and cluster
acceptance SHALL be refused, for any requirement that is
criticality-rated, that failed a quality check, or that originates
from a source of class `degraded`.

### 4.5 Splitting of supplied requirements

#### R-310-090

```yaml
id: R-310-090
version: 1
status: draft
category: regulatory
```

Splitting a supplied requirement SHALL create fragment entities linked
to it by a `splits-from` edge, and SHALL NOT modify the supplied
requirement's text, identifier, or source anchor.

**Rationale.** A supplied requirement is a contractual artefact
belonging to its issuer. Rewriting it destroys the ability to
demonstrate, at a contract review, what was received versus what was
interpreted.

#### R-310-091

```yaml
id: R-310-091
version: 1
status: draft
category: functional
derives-from: [R-310-090, R-310-110]
```

Each fragment SHALL declare the text interval of the **untranslated**
source requirement that it restates.

#### R-310-092

```yaml
id: R-310-092
version: 1
status: draft
category: functional
derives-from: [R-310-091]
```

A split SHALL be rejected unless the union of its fragments' intervals
covers the source text in full and no two intervals overlap.

**Rationale.** This is the only check that prevents a clause from
being lost during splitting. The failure is otherwise invisible: the
coverage matrix would show every remaining fragment as covered.
Expressed as interval arithmetic it is deterministic, requiring no
model judgement.

#### R-310-093

```yaml
id: R-310-093
version: 1
status: draft
category: functional
```

Splitting SHALL be proposed only for a requirement whose atomicity
check failed, or on explicit human request.

**Rationale.** Unconstrained splitting becomes the default reflex,
fragmenting atomic requirements and multiplying objects for no
traceability gain. Binding it to a failed check makes each split
self-justifying.

#### R-310-094

```yaml
id: R-310-094
version: 1
status: draft
category: functional
derives-from: [R-310-063]
```

Splitting a supplied requirement SHALL also emit a rework request to
its issuer citing the failed atomicity criterion.

**Rationale.** A non-atomic requirement is a defect in the supplied
specification. Correcting it only internally absorbs the issuer's debt
silently and leaves no evidence at contract review of why the
requirement was interpreted.

#### R-310-095

```yaml
id: R-310-095
version: 1
status: draft
category: functional
```

Fragment identifiers SHALL occupy a namespace visibly distinct from
supplied identifiers, SHALL be displayed with an origin marker, and
SHALL be excluded from exports addressed to the issuing party by
default.

**Rationale.** Returning to a customer requirements they did not write,
numbered like their own, is a contractual confusion risk.

#### R-310-096

```yaml
id: R-310-096
version: 1
status: draft
category: functional
derives-from: [R-310-064]
```

The coverage state of a split requirement SHALL be computed as the
aggregate of its fragments' coverage states, and SHALL NOT be settable
directly.

### 4.6 Multilingual corpora

#### R-310-110

```yaml
id: R-310-110
version: 1
status: draft
category: functional
```

When a supplied requirement is not in the corpus working language, the
platform SHALL store the translation as a derived object linked by a
`translation-of` edge, and the untranslated requirement SHALL remain
the authoritative text.

#### R-310-111

```yaml
id: R-310-111
version: 1
status: draft
category: functional
```

Objects produced by authoring workflows SHALL default to English.

#### R-310-112

```yaml
id: R-310-112
version: 1
status: draft
category: functional
derives-from: [R-310-110]
```

A change to a translation object SHALL mark every downstream object
covering it as `stale`.

**Rationale.** The whole cascade rests on the translation. A
translation error otherwise corrupts every downstream coverage claim
without any signal. Reusing the existing suspect-link mechanism
requires no additional machinery.

### 4.7 Document authoring

#### R-310-120

```yaml
id: R-310-120
version: 1
status: draft
category: functional
derives-from: [R-310-064]
```

An authoring workflow SHALL NOT complete while any requirement
allocated to its container lacks at least one covering object.

#### R-310-121

```yaml
id: R-310-121
version: 1
status: draft
category: functional
```

A `covers` edge SHALL be rejected when its target requirement is not
allocated to the container holding the source object.

#### R-310-122

```yaml
id: R-310-122
version: 1
status: draft
category: functional
```

The platform SHALL detect and surface **weak coverage** — an object
that references a requirement without answering it — as a state
distinct from an ordinary covered object.

**Rationale.** An agent readily writes *"in accordance with REQ-118,
the system brakes"*, which declares coverage and demonstrates none. Left
undetected, this is what turns the coverage matrix green and
untrustworthy, which is worse than leaving it red.

#### R-310-123

```yaml
id: R-310-123
version: 1
status: draft
category: functional
```

An authoring workflow SHALL place a human gate on the container
outline before drafting object bodies.

**Rationale.** The outline gate costs minutes and intercepts a framing
error before the cost of drafting is incurred.

#### R-310-124

```yaml
id: R-310-124
version: 1
status: draft
category: functional
```

Rejecting an object at a review gate SHALL re-run the authoring step
scoped to that object alone, and SHALL NOT regenerate the container.

**Rationale.** Review throughput, not generation throughput, is the
binding constraint. If each rejection costs a full container
regeneration, the cost of rejecting rises until reviewers stop
rejecting.

#### R-310-125

```yaml
id: R-310-125
version: 1
status: draft
category: functional
derives-from: [R-310-068, R-310-120]
```

When a requirement is returned per `R-310-068`, it SHALL enter a
`waiting-reallocation` state that suspends only the completeness check
of `R-310-120`, and authoring of the container's remaining objects
SHALL continue.

**Rationale.** A blocking check with no exit is satisfied by writing an
empty object. Providing the exit is what prevents the circumvention
that would make the whole mechanism decorative. Freezing the container
instead would make one return stall a whole document.

### 4.8 Change absorption

#### R-310-140

```yaml
id: R-310-140
version: 1
status: draft
category: functional
```

On intake of a source already represented in the current baseline, the
platform SHALL diff it against that baseline and open exactly one
change ticket per added, modified or removed requirement.

#### R-310-141

```yaml
id: R-310-141
version: 1
status: draft
category: architecture
derives-from: [R-310-002]
```

The impact set of a change SHALL be computed by deterministic
traversal of the coverage and derivation graph, and SHALL NOT be
produced, extended or filtered by an agent.

**Rationale.** Determining what is impacted is graph arithmetic. An
agent asked to do it returns a plausible and incomplete set, and the
omission is undetectable. Agents qualify impact (`R-310-148`); they do
not discover it. This mirrors the platform rule that gate evidence is
observed, never inferred (`R-200-012`).

#### R-310-142

```yaml
id: R-310-142
version: 1
status: draft
category: functional
derives-from: [R-310-141]
```

Traversal SHALL list every reachable node, and a cycle encountered
during traversal SHALL be reported as a graph defect rather than
traversed.

#### R-310-143

```yaml
id: R-310-143
version: 1
status: draft
category: functional
derives-from: [R-310-064]
```

A node reachable by more than one path SHALL appear once in the impact
set and SHALL display every path by which it was reached.

**Rationale.** Multi-allocation makes branches converge, so the impact
set is a DAG. Showing a single cause would let a reviewer accept a node
having seen half the reason it changed.

#### R-310-144

```yaml
id: R-310-144
version: 1
status: draft
category: functional
derives-from: [R-310-143]
```

The closure progress counter SHALL count distinct nodes.

**Rationale.** Counting paths instead of nodes inflates the
denominator, and a gate whose denominator can never be reached cannot
be closed.

#### R-310-145

```yaml
id: R-310-145
version: 1
status: draft
category: functional
```

A coverage link SHALL pin the version of its target, and SHALL be
marked `stale` when the target's current version exceeds the pinned
version.

#### R-310-146

```yaml
id: R-310-146
version: 1
status: draft
category: functional
derives-from: [R-310-144, R-310-150]
```

A change ticket SHALL NOT be closable while any node of its impact set
lacks a disposition, any coverage link in that set remains `stale`, or
any requirement became uncovered as a result of the change.

#### R-310-147

```yaml
id: R-310-147
version: 1
status: draft
category: functional
derives-from: [R-310-092]
```

When a change modifies a requirement that has been split, the
exhaustiveness check of `R-310-092` SHALL be re-run against the new
source text and the existing fragment intervals.

**Rationale.** A new version may add a clause falling outside every
existing fragment interval. Re-checking only the fragments would miss
it, silently.

#### R-310-148

```yaml
id: R-310-148
version: 1
status: draft
category: functional
derives-from: [R-310-141]
```

For each node of the impact set, an agent SHALL propose a
qualification drawn from `no-effect`, `rewording`,
`substantive-rework`, or `architecture-decision-required`, with a
justification, and that qualification SHALL be presented to a human
gate before any rework is performed.

#### R-310-149

```yaml
id: R-310-149
version: 1
status: draft
category: architecture
derives-from: [R-310-140, R-310-141]
```

A change ticket SHALL be created, propagated and closed by the
traceability mechanism itself, and the platform SHALL NOT expose
creation, assignment, prioritisation or manual status mutation of a
change ticket.

**Rationale.** The ticket is derived state: it exists because a
supplied requirement changed, its scope is the traversal result, and
its closure condition is mechanical. Exposing it as a hand-managed work
item would create a second, divergent source of truth about what
remains to be done, and would allow a ticket to be closed without the
evidence that closing it is supposed to constitute.

#### R-310-150

```yaml
id: R-310-150
version: 1
status: draft
category: regulatory
derives-from: [R-310-010, R-310-148]
```

Each node of an impact set SHALL carry a disposition of either
`modified-and-accepted` or `confirmed-unchanged`, and a
`confirmed-unchanged` disposition SHALL record its actor, timestamp and
justification.

**Rationale.** Deciding that a node needs no change is a review
outcome, not the absence of one, and it is the outcome that occurs most
often. It must be recorded as positively as a modification — otherwise
a closed ticket cannot distinguish "examined and found unaffected" from
"never looked at", which is the entire value of closing it.

### 4.9 Conversational piloting

#### R-310-170

```yaml
id: R-310-170
version: 1
status: draft
category: functional
```

Before executing work on a batch of requirements or changes, an agent
SHALL propose a treatment plan and SHALL NOT begin execution until a
human has ratified it.

#### R-310-171

```yaml
id: R-310-171
version: 1
status: draft
category: functional
impacts: [E-310-004]
```

A treatment plan SHALL declare the batch it concerns, its decomposition
into steps, an effort classification per step, the execution mode
proposed per step, and an estimate of token cost, monetary cost,
duration, and the number of review items the plan will generate.

**Rationale.** At the volume of `R-310-300`, ratifying "process
everything end to end" without an estimate is a blank cheque on both
the LLM budget and the reviewer's time. The review-item count is the
estimate that matters most, because reviewer capacity binds before
budget does.

#### R-310-172

```yaml
id: R-310-172
version: 1
status: draft
category: functional
derives-from: [R-310-171]
```

The execution mode of each step SHALL be a property of the ratified
plan, selectable between end-to-end execution and step-by-step
execution with a gate per step.

**Rationale.** Whether a downstream layer may start before its upstream
is fully accepted is not a fixed platform property: two small timing
edits warrant one gate at the end, an architecture change warrants a
gate per layer. Deciding it per batch, with the agent proposing from its
effort classification, is strictly better than a global constant.

#### R-310-173

```yaml
id: R-310-173
version: 1
status: draft
category: functional
derives-from: [R-310-172]
```

A ratified plan SHALL be amendable during execution, and re-splitting
one step SHALL NOT invalidate steps already completed.

**Rationale.** The effort classification is a model estimate and will
sometimes be wrong. Without mid-flight amendment the user is held to a
plan produced by a bad estimate.

#### R-310-174

```yaml
id: R-310-174
version: 1
status: draft
category: functional
derives-from: [R-310-171]
```

A step that exceeds its estimated token cost, object count or
review-item count by a configured margin SHALL suspend and return to
the user rather than continue.

**Rationale.** A silent overrun is how a budget is consumed without
anyone noticing.

#### R-310-175

```yaml
id: R-310-175
version: 1
status: draft
category: regulatory
derives-from: [R-310-170]
```

Ratification of a treatment plan SHALL be recorded as a decision
attached to the run, carrying the actor, the timestamp and the plan
version, independently of the conversation in which it was expressed.

**Rationale.** A transcript is not a record. In a regulated context,
the evidence that an arbitration took place must be an attributable
entry against the work, not a message in a thread.

#### R-310-176

```yaml
id: R-310-176
version: 1
status: draft
category: functional
```

On completion of a treatment plan, the platform SHALL produce a
treatment report naming the requirements processed, the containers
modified, the objects created and their review outcomes, the coverage
delta, the remaining gaps, and any allocation returns awaiting
arbitration.

#### R-310-177

```yaml
id: R-310-177
version: 2
status: draft
category: functional
derives-from: [R-310-172]
```

Under end-to-end execution, an object covering an upstream object that
is not `accepted` SHALL be marked `speculative`, and SHALL become
`stale` if that upstream object changes before acceptance.

**Clarification (v2).** `speculative` is a marking about **provenance** —
what the object was built on — and is NOT a member of the review-state
set of `R-310-006`, which remains the five states it names. The two are
orthogonal and must be, because end-to-end execution routinely produces
an object that is both: a reviewer accepts an architecture object while
the functional object above it is still `proposed`. A sixth review state
would make that situation unrepresentable and force a choice between
recording "this was examined" and recording "this was built on sand".

The marking is **derived, never stored**: an object is speculative when
any of its coverage links targets an object that is neither `accepted`
nor `auto-accepted`. A stored flag would drift the moment an upstream was
accepted without the flag being updated — the same failure that makes the
staleness of `R-310-145` a computed property rather than a column.

The second clause is the existing mechanism: when the upstream advances
past the version its coverage link pinned, the link is already suspect by
`R-310-145`, and a speculative object whose upstream has advanced SHALL
have its own review state set to `stale`.

### 4.10 Concurrency

#### R-310-190

```yaml
id: R-310-190
version: 1
status: draft
category: functional
```

Editing an object SHALL require acquiring an exclusive lock on that
object, granted as a lease with a finite expiry that is renewed by
continued activity.

**Rationale.** A lock without expiry is held forever by an actor whose
session ended.

#### R-310-191

```yaml
id: R-310-191
version: 1
status: draft
category: functional
derives-from: [R-310-190]
```

An agent SHALL acquire the same object lock as a human actor, and
SHALL be identified as the holder.

**Rationale.** The concurrent-edit hazard is not human-to-human only;
an agent redrafting an object while a reviewer edits it produces the
same lost update.

#### R-310-192

```yaml
id: R-310-192
version: 1
status: draft
category: functional
derives-from: [R-310-190, R-300-060]
```

Object writes SHALL additionally verify the object's expected version,
and SHALL be rejected on mismatch even when a lock was held.

**Rationale.** Leases expire. The lock prevents wasted work; the
version check preserves correctness when the lock has lapsed.

#### R-310-193

```yaml
id: R-310-193
version: 1
status: draft
category: ux
derives-from: [R-310-190]
```

A held lock SHALL be visible to other actors with its holder and
expiry, and the container owner SHALL be able to force its release.

**Rationale.** An invisible lock is experienced as a malfunction.

### 4.11 Baselines, retention and rendering

#### R-310-200

```yaml
id: R-310-200
version: 1
status: draft
category: functional
```

A baseline SHALL consist of a tag and a manifest enumerating each
included object version, each coverage link with its pinned version,
and content hashes, and SHALL NOT duplicate object content.

#### R-310-201

```yaml
id: R-310-201
version: 1
status: draft
category: functional
derives-from: [R-310-146]
```

Creating a baseline SHALL be refused while any change ticket is open,
any criticality-rated requirement carries a coverage gap, or any
coverage link in the corpus is `stale`.

#### R-310-202

```yaml
id: R-310-202
version: 1
status: draft
category: regulatory
derives-from: [R-310-010]
```

Every object version SHALL be retained in full for the life of the
project, and no compaction, pruning or summarisation of the object
version chain SHALL be performed.

**Rationale.** Because versioning is decision-triggered (`R-310-010`),
every version already carries a human decision and therefore audit
value — there is nothing left to compact. This is the direct consequence
of not versioning negotiation iterations: the retention problem
disappears instead of being managed.

#### R-310-203

```yaml
id: R-310-203
version: 1
status: draft
category: functional
derives-from: [R-310-009]
```

A working draft SHALL be discarded once the negotiation that produced
it is resolved by acceptance, by rejection, or by confirmation that no
change is required.

**Rationale.** The working draft is scratch space. What survives is the
accepted version (`R-310-010`) and the negotiation trace in the run
record (`R-310-011`); keeping the draft as well would reintroduce the
storage growth the model exists to avoid.

#### R-310-204

```yaml
id: R-310-204
version: 1
status: draft
category: regulatory
derives-from: [R-310-202]
```

An object version referenced by any baseline manifest SHALL NOT be
deleted.

**Rationale.** It is the artefact an audit reconstructs. Its
availability is the point of taking a baseline at all. Stated
separately from `R-310-202` so that it survives any future relaxation
of general retention.

#### R-310-205

```yaml
id: R-310-205
version: 1
status: draft
category: architecture
derives-from: [R-310-202]
```

Retained object versions SHALL be stored as complete compressed
documents, and SHALL NOT be stored as deltas against another version.

**Rationale.** At the volume of `R-310-303` the storage saving from
delta encoding is not needed, while the chain dependency it introduces
turns the loss of one stored version into the loss of all subsequent
history and complicates reindex. Compression yields a comparable saving
with no chain dependency. This resolves `Q-300-002`.

#### R-310-206

```yaml
id: R-310-206
version: 1
status: draft
category: functional
derives-from: [R-310-009]
```

The number of working-draft iterations an agent may produce for one
object within one negotiation SHALL be capped, and reaching the cap
SHALL be reported as a run anomaly rather than silently permitted.

**Rationale.** Iterations no longer cost storage (`R-310-009`), but an
agent that rewrites one paragraph forty times without converging is
still a defect to surface — and it costs tokens on every attempt.

#### R-310-207

```yaml
id: R-310-207
version: 1
status: draft
category: functional
derives-from: [R-310-200]
```

Rendering to DOCX and PDF SHALL be produced from a baseline manifest
using a platform-supplied base template.

### 4.12 Functional UI requirements

> These state what the activity requires of the interface. Their
> realisation — panel geometry, keyboard bindings, tab behaviour —
> belongs to `500-SPEC-UI-UX.md` and requires an amendment there.

#### R-310-220

```yaml
id: R-310-220
version: 1
status: draft
category: ux
```

The engineering activity SHALL be conducted within a single workbench
surface presenting container navigation, document editing, change
review, coverage matrix and conversation concurrently, without
navigation between separate pages.

#### R-310-221

```yaml
id: R-310-221
version: 1
status: draft
category: ux
derives-from: [R-310-220]
```

The conversation and the workbench panels SHALL share one selection
context, such that an object named by an agent is revealed in the
panels and an object selected in the panels is addressable in the
conversation without restating its identifier.

**Rationale.** Without shared selection the user spends the interaction
re-designating in words what is already on screen, which is the cost
the single surface exists to remove.

#### R-310-222

```yaml
id: R-310-222
version: 1
status: draft
category: ux
```

A coverage link displayed on an object SHALL be expandable in place to
reveal the target's text, source anchor, status and allocations,
without leaving the container being read.

#### R-310-223

```yaml
id: R-310-223
version: 1
status: draft
category: ux
derives-from: [R-310-063]
```

The workbench SHALL present a permanently available findings panel
listing coverage gaps, suspect links, weak coverage, failed splits and
failed checks, each citing its criterion identifier and its location.

#### R-310-224

```yaml
id: R-310-224
version: 1
status: draft
category: ux
derives-from: [R-310-120]
```

While a container is open, the workbench SHALL display the
requirements allocated to it that are not yet covered.

#### R-310-225

```yaml
id: R-310-225
version: 1
status: draft
category: ux
derives-from: [R-310-007]
```

Any displayed coverage figure SHALL display alongside it the share of
that coverage derived from `auto-accepted` links.

#### R-310-226

```yaml
id: R-310-226
version: 1
status: draft
category: ux
derives-from: [R-310-070]
```

The coverage matrix SHALL open grouped by cluster when the corpus
exceeds a configured requirement count, and SHALL be expandable to
individual requirements.

---

## 5. Non-Functional Requirements

#### R-310-300

```yaml
id: R-310-300
version: 1
status: draft
category: nfr
```

The platform SHALL support a project carrying up to 30 000 supplied
requirements, with the derived fragment, object and edge counts that
implies.

**Rationale.** The operating assumption stated for the target
engagements. Every mechanism in this document that would not survive
that volume — exhaustive intake review chief among them — is
constrained accordingly (`R-310-070`).

#### R-310-301

```yaml
id: R-310-301
version: 1
status: draft
category: nfr
derives-from: [R-310-300, R-310-141]
```

Impact traversal for a single change SHALL complete within 2 seconds
at the volume of `R-310-300`.

**Rationale.** The traversal is on the interactive path: a reviewer
opens a change ticket and expects its impact set. Beyond a couple of
seconds the workflow is experienced as a batch job and users stop
opening tickets.

#### R-310-302

```yaml
id: R-310-302
version: 1
status: draft
category: nfr
derives-from: [R-310-226]
```

The coverage matrix SHALL render its first page within 2 seconds at
the volume of `R-310-300`, paginating or clustering rather than
returning the full matrix.

**Rationale.** A 30 000-row matrix cannot be transferred or rendered
whole; the requirement forces the server-side aggregation that
`R-310-226` depends on.

#### R-310-303

```yaml
id: R-310-303
version: 1
status: draft
category: nfr
derives-from: [R-310-010, R-310-205]
```

Object storage including the full retained version chain SHALL remain
within 10 GB per project at the volume of `R-310-300`.

**Rationale.** Approximately 150 000 objects at 1–4 KB per version.
Because versions are created only on a review decision (`R-310-010`),
their count per object is bounded by human review activity rather than
by agent iteration — on the order of units, not hundreds. With the
compression of `R-310-205` this sits an order of magnitude inside the
envelope. The threshold exists to make a future regression visible, not
to constrain the design.

#### R-310-304

```yaml
id: R-310-304
version: 1
status: draft
category: nfr
derives-from: [R-310-171]
```

A treatment plan's cost estimate SHALL be within 30 % of the observed
cost for steps whose effort classification was not amended during
execution.

**Rationale.** An estimate that cannot be relied upon within an order
of magnitude turns ratification into a formality, which defeats
`R-310-170`. The tolerance is wide because the estimate is a model
output; the overrun guard of `R-310-174` handles the tail.

#### R-310-305

```yaml
id: R-310-305
version: 1
status: draft
category: regulatory
```

Every state transition of an object, a coverage link, an allocation
and a change ticket SHALL be attributable to an actor and a timestamp,
and SHALL be reconstructible for any point in time not earlier than
the corpus creation.

**Rationale.** This is the property the whole document exists to
provide. It is stated as a requirement so that it is testable rather
than assumed.

---

## 6. Interfaces & Contracts

### E-310-001: Object document schema

```yaml
id: E-310-001
version: 1
status: draft
category: architecture
```

The MinIO-resident object document, source of truth per `R-310-001`.

```json
{
  "object_id": "OBJ-1120",
  "project_id": "<project-id>",
  "container": "030-ARCHITECTURE-DESIGN",
  "type": "paragraph",
  "parent": "OBJ-1118",
  "ordinal": 3,
  "version": 12,
  "review_state": "stale",
  "body": "The primary actuator shall deliver up to 140 Nm …",
  "notation": null,
  "produced_by": { "workflow": "WF-002", "workflow_version": 3 },
  "cycle_version": 4,
  "created_at": "2026-09-28T09:11:04Z",
  "created_by": "<actor-id>",
  "updated_at": "2026-09-29T11:02:41Z",
  "updated_by": "agent:architect"
}
```

`notation` carries the textual source of a `figure` object per
`R-310-008` and is null for every other type. `body` is null for a
`figure`.

A **working draft** (`R-310-009`) is stored separately, at
`…/objects/<object-id>.draft.json`, carrying the same shape plus the
negotiation identifier and the iteration counter. It is not a version,
it is overwritten on each iteration, and it is removed when the
negotiation resolves (`R-310-203`). `version` on the object document
therefore only ever advances on a review decision (`R-310-010`).

### E-310-002: Cycle definition schema

```yaml
id: E-310-002
version: 1
status: draft
category: architecture
```

The `C-` entity of `R-310-020`, published at tenant scope.

```yaml
id: C-AUTOMOTIVE
version: 4
status: approved
containers:
  - slug: 010-FUNCTIONAL-DESCRIPTION
    ordinal: 10
    scope_id: SC-001
    scope: >
      Observable system behaviour at vehicle level. Excludes component
      partitioning and signal-level detail.
    coverage_obligatory: true
    links_out: [derives-from]
    links_in: [covers]
  - slug: 030-ARCHITECTURE-DESIGN
    ordinal: 30
    scope_id: SC-002
    scope: >
      Component partitioning, allocation of behaviour to components,
      redundancy and independence arguments.
    coverage_obligatory: true
    links_out: [derives-from]
    links_in: [covers]
```

### E-310-003: Workflow definition schema

```yaml
id: E-310-003
version: 1
status: draft
category: architecture
```

The `WF-` entity of `R-310-040`. `checks` is non-empty per
`R-310-041`; `steps[].kind` is constrained by `R-310-042` and control
flow by `R-310-043`.

```yaml
id: WF-002
version: 3
status: approved
intent: >
  Produce or complete a container so that every requirement allocated
  to it is covered by at least one object.
inputs:
  container: any
  requires: [allocated_requirements, upstream_containers]
steps:
  - id: s1
    kind: check
    ref: CRIT-PRE-001
  - id: s2
    kind: agent
    role: doc-author
    action: propose_outline
  - id: s3
    kind: human-gate
    name: Outline review
    on_reject: s2
  - id: s4
    kind: agent
    role: doc-author
    action: draft_objects
  - id: s5
    kind: check
    ref: [CRIT-COV-001, CRIT-COV-002, CRIT-STR-001]
  - id: s6
    kind: agent
    role: doc-author
    action: detect_weak_coverage
  - id: s7
    kind: human-gate
    name: Per-object review
    on_reject: s4
    reject_scope: object
outputs:
  objects: [heading, paragraph, list, table, figure]
  edges: [covers]
  initial_state: proposed
checks:
  - id: CRIT-COV-001
    statement: Every allocated requirement has at least one covering object.
  - id: CRIT-COV-002
    statement: No covers edge targets a requirement not allocated to this container.
  - id: CRIT-STR-001
    statement: Every object has a parent within the accepted outline.
examples:
  - ref: examples/wf-002/architecture-section.json
```

### E-310-004: Treatment plan schema

```yaml
id: E-310-004
version: 1
status: draft
category: architecture
```

The negotiated plan of `R-310-170` / `R-310-171`.

```json
{
  "plan_id": "TP-0114",
  "version": 2,
  "batch": { "kind": "change_set", "source": "CUST-2026-W14", "count": 340 },
  "steps": [
    {
      "step_id": "st1",
      "scope": { "change_tickets": 287 },
      "effort": "batchable",
      "mode": "end-to-end",
      "estimate": { "tokens": 4200000, "cost_eur": 31.4,
                    "duration_min": 48, "review_items": 612 }
    },
    {
      "step_id": "st2",
      "scope": { "change_tickets": 53 },
      "effort": "reflection-required",
      "mode": "step-by-step",
      "estimate": { "tokens": 1900000, "cost_eur": 22.8,
                    "duration_min": 0, "review_items": 200 }
    }
  ],
  "ratified_by": "<actor-id>",
  "ratified_at": "2026-09-29T08:41:12Z"
}
```

`effort` is drawn from `batchable`, `arbitration-required`,
`reflection-required`. `duration_min` is null or zero for
step-by-step steps, whose duration is gated by reviewer availability.

### E-310-005: Criterion registry

```yaml
id: E-310-005
version: 1
status: draft
category: architecture
```

Every finding surfaced to a reviewer cites a criterion identifier per
`R-310-063` and `R-310-223`. Criteria are declared by the workflows
that check them; the registry is the union, and is versioned with the
workflow catalogue.

| Criterion | Statement | Raised by |
|---|---|---|
| `CRIT-SRC-001` | Every extracted requirement carries a resolvable source anchor. | WF-001 |
| `CRIT-QUA-004` | A requirement contains no unbounded term where a quantified bound is required. | WF-001 |
| `CRIT-ALO-001` | No accepted requirement lacks an allocation or an out-of-project verdict. | WF-001 |
| `CRIT-ALO-002` | Every allocation cites an existing container `scope_id`. | WF-001 |
| `CRIT-SPL-002` | Fragment intervals cover the source text exactly and without overlap. | WF-001, WF-003 |
| `CRIT-COV-001` | Every allocated requirement has at least one covering object. | WF-002 |
| `CRIT-COV-002` | No `covers` edge targets an unallocated requirement. | WF-002 |
| `CRIT-COV-004` | An object declaring coverage demonstrably answers its target. | WF-002 |
| `CRIT-TRC-001` | Impact traversal is exhaustive and acyclic. | WF-003 |
| `CRIT-TRC-003` | No coverage link remains pinned behind its target's version. | WF-003 |
| `CRIT-CLO-001` | No node of the impact set is unreviewed. | WF-003 |

### E-310-006: ArangoDB collections

```yaml
id: E-310-006
version: 2
status: draft
category: architecture
```

Derived index per `R-310-002`, owned exclusively by the Requirements
Service under the ownership rule of `R-100-012`. All collections are
rebuildable from MinIO.

| Collection | Kind | Holds |
|---|---|---|
| `req_objects` | document | one record per object, keyed `<project>:<object-id>` |
| `req_object_edges` | edge | `contains`, `covers`, `derives-from`, `splits-from`, `translation-of` |
| `req_allocations` | document | `allocated-to`, `allocation-rejected`, and the returns that replay them |
| `req_object_locks` | document | edit leases; NOT backed up (recomputable, worthless after expiry) |
| `req_cycles` | document | published and draft `C-` versions |
| `req_workflows` | document | published and draft `WF-` versions |
| `req_changes` | document | change tickets and their impact sets |
| `req_plans` | document | treatment plans and their ratifications |
| `req_baselines` | document | baseline manifests |

**Why `req_allocations` is a document collection and `req_object_edges`
is not** (v2, correcting v1). An ArangoDB edge needs a vertex at both
ends. `req_object_edges` has them: object to object. An allocation's
ends are a supplied requirement and a *container* — and a container is
not a vertex, it is a name in a published cycle (`E-310-002`), with no
row of its own to point `_to` at. Modelling it as an edge would have
forced a phantom vertex per container whose only purpose was to be
pointed at, and that vertex would then have had to be kept in step with
the cycle version that defines it. An allocation also carries a verdict,
an acceptance, an out-of-project ruling and an accumulating list of
returns, which is a record's shape, not a link's.

### E-310-007: Treatment report

```yaml
id: E-310-007
version: 1
status: draft
category: architecture
```

The closure artefact of `R-310-176`, and the document exported when an
issuing party asks for progress against its supplied requirements.

| Field | Content |
|---|---|
| `batch` | the source drop or change set processed |
| `requirements` | counts by outcome: accepted, auto-accepted, returned, out-of-project |
| `containers_modified` | one row per container with objects created, accepted, pending |
| `coverage_delta` | coverage before and after, with the auto-accepted share of each |
| `remaining_gaps` | requirement, container, criterion |
| `pending_arbitration` | allocation returns awaiting a decision per `R-310-069` |
| `dispositions` | per closed change ticket, the count of nodes `modified-and-accepted` versus `confirmed-unchanged`, the latter being the evidence that an unmodified node was nonetheless examined (`R-310-150`) |

---

## 7. Open Questions

| ID | Question | Owning decision | Target resolution |
|---|---|---|---|
| Q-310-001 | Which notation for `figure` objects — Mermaid, PlantUML, or both behind one adapter? Both render to SVG; PlantUML covers more engineering diagram kinds, Mermaid needs no runtime. | — | v1 |
| Q-310-002 | Does clustering for review (`R-310-070`) group by proposed allocation alone, or also by embedding similarity of requirement text? The latter needs C7 and changes the dependency graph of intake. | — | v1 |
| Q-310-003 | Is the `waiting-reallocation` state of `R-310-125` visible to the issuing party in a treatment report, or internal only? | — | v1 |
| Q-310-004 | Which role publishes a cycle and a workflow? The five-role taxonomy of `E-100-002` has no method-engineer role; adding one touches the route catalogue of `CLAUDE.md` §13. | — | v1 |
| Q-310-005 | Does OCR for `degraded` sources run in-platform or as a C12 ingestion workflow? The latter keeps the dependency out of the API tier. | — | v1 |
| Q-310-006 | Customer-supplied DOCX templates for rendering (`R-310-207`) — deferred to v2, or required at first delivery? Filling a supplied template is a materially different implementation from rendering a platform template. | — | v2 |
| Q-310-007 | Retention of unpublished `C-` / `WF-` draft versions, which are not covered by the decision-triggered model of `R-310-010`. Extends `Q-300-004`. | — | v2 |
| Q-310-008 | Does the effort classification of `R-310-148` / `R-310-171` come from a model judgement, or from deterministic features (impact-set size, criticality, layers crossed)? Deterministic features are auditable and cheaper; a model may classify better. | — | v1 |

---

## 8. Appendices

### 8.1 Validation artifacts

#### T-310-001

```yaml
id: T-310-001
version: 1
status: draft
category: functional
domain: documentation
```

Given a supplied requirement and a proposed split, the exhaustiveness
check rejects the split when the union of fragment intervals omits any
character of the source text, and rejects it when any two intervals
overlap. Validates `R-310-092`.

#### T-310-002

```yaml
id: T-310-002
version: 1
status: draft
category: functional
domain: documentation
```

Given a graph in which one object is reachable from a changed
requirement by two distinct paths, the impact set contains that object
exactly once and enumerates both paths, and the closure counter
denominator equals the distinct node count. Validates `R-310-143`,
`R-310-144`.

#### T-310-003

```yaml
id: T-310-003
version: 1
status: draft
category: functional
domain: documentation
```

A change ticket with at least one unreviewed node, one `stale`
coverage link, or one requirement made uncovered is refused closure,
and the refusal names which precondition failed. Validates
`R-310-146`.

#### T-310-004

```yaml
id: T-310-004
version: 1
status: draft
category: functional
domain: documentation
```

A workflow definition whose `checks` list is empty is refused
publication; a workflow containing a conditional or loop construct is
refused parsing. Validates `R-310-041`, `R-310-043`.

#### T-310-005

```yaml
id: T-310-005
version: 1
status: draft
category: functional
domain: documentation
```

A sequence of negotiation iterations on one object produces no object
version; accepting the working draft produces exactly one version
carrying the reviewer and timestamp; and the working draft is no longer
readable after resolution. Validates `R-310-009`, `R-310-010`,
`R-310-203`.

#### T-310-006

```yaml
id: T-310-006
version: 1
status: draft
category: functional
domain: documentation
```

An allocation proposal citing no existing `scope_id` is rejected
before surfacing; a second return of the same requirement escalates to
arbitration instead of re-allocating. Validates `R-310-066`,
`R-310-069`.

#### T-310-007

```yaml
id: T-310-007
version: 1
status: draft
category: security
domain: documentation
```

A fragment identifier is absent from an export addressed to the
issuing party, and present in an internal export. Validates
`R-310-095`.

**Rationale.** The failure this guards against is a contractual one:
returning to a customer requirements it never wrote, numbered like its
own. It is invisible in the platform and only surfaces at a contract
review, so it must be caught by an automated check.

#### T-310-008

```yaml
id: T-310-008
version: 1
status: draft
category: regulatory
domain: documentation
```

A change ticket whose every impact node carries a
`confirmed-unchanged` disposition is closable, and the closed ticket
exposes the actor, timestamp and justification of each such
disposition; a ticket with one undispositioned node is refused closure.
Validates `R-310-146`, `R-310-150`.

**Rationale.** A closed ticket is the evidence that a supplied
modification was taken into account end to end. If a node could be
closed without a recorded disposition, that evidence would assert a
review that never happened — the failure mode `R-200-012` exists to
forbid.

#### T-310-009

```yaml
id: T-310-009
version: 1
status: draft
category: functional
domain: documentation
```

No exposed interface permits creating, assigning, prioritising or
setting the status of a change ticket. Validates `R-310-149`.

### 8.2 Amendments required in other documents

| Document | Amendment | Driven by |
|---|---|---|
| `300-SPEC-REQUIREMENTS-MGMT.md` | `R-300-080` extended to DOCX, PDF and XLSX import with confidence classes. `Q-300-002` closed by `R-310-205`. | `R-310-060`, `R-310-205` |
| `500-SPEC-UI-UX.md` | **Done 2026-10-01** — `R-500-015`…`R-500-021` added in v7, realising the workbench surface of §4.12. | `R-310-220`…`R-310-226` |
| `999-SYNTHESIS.md` | **Done** — `D-023`…`D-027` added to §5, `310` added to the §6 document mapping. | §2, §8.3 |
| `100-SPEC-ARCHITECTURE.md` | **No amendment needed** (established 2026-10-01, v2 of this document). This row asked for a "collection ownership table" to be extended; `100-SPEC` has no such table. `R-100-012` v3 assigns ownership *generically* — "the Requirements Service (C5) owns the requirements collections and the `requirements` bucket" — which already covers every collection of `E-310-006` without enumeration, and enumerating them there would create a second list able to drift from `E-310-006`. | `E-310-006` |

### 8.3 Decision coverage

Every cross-cutting choice this document rests on is anchored in
`999-SYNTHESIS.md` §5. The table maps each decision to the sections
that realise it, so that a reader can verify no section of this
document introduces an unanchored cross-cutting choice.

| Decision | Realised by |
|---|---|
| D-023 — object-grain document model, decision-triggered versioning | §4.1, §4.11, `E-310-001` |
| D-024 — cycle and workflow as versioned publishable entities | §4.2, §4.3, `E-310-002`, `E-310-003` |
| D-025 — multi-container allocation, non-destructive splitting, cluster review | §4.4, §4.5, §4.6 |
| D-026 — change absorption as traceability machinery | §4.8, `E-310-007` |
| D-027 — lease-based object locking | §4.10 |
| D-005 — platform → project hierarchy | `R-310-022`, `R-310-046` |
| D-012 — domain-agnostic backbone | §1 (relationship to 300-SPEC), `R-310-021` |

---

**End of 310-SPEC-DOC-TRACEABILITY.md v1.**
