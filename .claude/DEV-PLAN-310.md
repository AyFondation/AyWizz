<!-- =============================================================================
File: DEV-PLAN-310.md
Version: 7
Path: .claude/DEV-PLAN-310.md
Description: Living development plan for 310-SPEC-DOC-TRACEABILITY.
             Amended at the end of every working slice so no session
             loses the thread. Sections:
               §1  increment board (status per increment)
               §2  current increment, sliced into verifiable steps
               §3  running log (append-only, newest last)
               §4  clarifications needed from the operator
               §5  decisions taken during dev (deviations from spec intent)

Discipline: §3 and §5 are APPEND-ONLY. §1 and §2 are updated in place.
            Every step's done-criterion is `run_tests.sh ci` green plus
            the named requirement IDs carrying `@relation implements:`
            markers. A step is NOT done until both hold.
============================================================================= -->

# Dev Plan — 310 Document Traceability

**Spec:** `requirements/310-SPEC-DOC-TRACEABILITY.md` v3
**Decisions:** `D-023`…`D-027` (`999-SYNTHESIS.md` v11)
**Started:** 2026-09-30

---

## 1. Increment board

| # | Increment | Requirements | Status |
|---|---|---|---|
| **1** | **Object model + storage + locking + REST** | `R-310-001…011`, `124`, `150`, `190…193`, `202…206` | **DONE** — CI green, coverage 89.89 % |
| **2** | **Cycle (`C-`) + workflow (`WF-`) entities + authoring API** | `R-310-020…025`, `040…047` | **DONE** — CI green, coverage 90.08 % |
| **3** | **Coverage graph + traceability queries** | `R-310-064…069`, `096`, `120…122`, `145` | **DONE** — CI green, coverage 90.28 % |
| **4** | **Intake + splitting** | `R-300-080` v2, `R-310-060…063`, `090…096` | **DONE** — CI green, coverage 90.36 %, 3419 tests |
| **5** | **Change absorption (DAG, dispositions, closure)** | `R-310-140…150` | **DONE** — CI green, coverage 90.44 %, 3593 tests |
| **6** | **Workflow execution + negotiated treatment plan + object-grain agent tools** | `R-310-025`, `148`, `170…177` | **DONE** — CI green, coverage 90.47 %, 3838 tests |
| **7** | **UI — the workbench shell** | `R-310-220…226` | **DONE** — UI ci green (553 tests, 4/4 thresholds), backend still green (3847) |
| **8** | **Baseline + DOCX/PDF rendering** | `R-310-200…207` | **DONE** — both CI gates green; all eight of §4.11 `tested` |
| **9** | **The requirements no increment covered** — audited out of `060-IMPLEMENTATION-STATUS.md`, not planned from the spec's table of contents | see below | **next** |

**Increment 9 exists because the board was not the spec.** Increments 1–8
were sliced from §4.1–§4.12 and all closed green, which made it easy to
read "done" as "complete". An audit of `060-IMPLEMENTATION-STATUS.md`
after increment 8 found 15 requirements no increment had claimed:

| Requirement | What is missing | Weight |
|---|---|---|
| `R-310-070`, `071` | Cluster review at intake, with `auto-accepted` on acceptance and a MANDATORY individual review for anything criticality-rated, quality-failed or `degraded`. | Substantial — and the one that matters most at 30 000 requirements, since exhaustive individual intake review is not achievable. |
| `R-310-110`, `111`, `112` | Multilingual corpora: `translation-of` edges, English default for authored objects, and a translation change marking downstream objects `stale`. | Moderate; `112` reuses the suspect-link mechanism. |
| `R-310-024` | One cycle version per baseline, with an explicit migration producing an impact report. | Moderate. |
| `R-310-123`, `125` | The outline human gate before drafting; `waiting-reallocation` suspending only the completeness check. | Small, both inside existing surfaces. |
| `R-310-044` | Prompts rendered from the workflow's declared fields, with no free-form prompt body exposed. | Small. |
| `R-310-300`…`305` | The non-functional envelope: 30 000 requirements, 2 s impact traversal, and the rest. | Needs a performance tier that does not exist yet — the largest single piece. |

None of these was deferred by a decision; they were simply never on the
board. Recorded here so the next session plans from the audit rather than
from the slice list.

**Ordering constraint.** Increment 1 is a hard prerequisite of every
other increment. Increments 3 and 4 may proceed in parallel once 1 and 2
land. Increment 7 needs 3 and 5 to have something to display.

**The "DocGen migration" is cancelled, not deferred.** The board once
carried it as increment 1's principal cost, on the strength of a
consequence clause in `D-023` that I wrote and that overreached. C-04
(§4) established by reading the code that live-docs are a different
surface which `310-SPEC` never asks to replace. `D-023` is amended
(`999-SYNTHESIS` v11): agents get object tools **alongside** DocGen.
What remains is a second tool catalogue, scheduled in increment 6 beside
its caller.

---

## 2. Current increment — 8. Baseline & rendering

Target: `c5_requirements/baseline/` plus the deferred UI mutations.
Increment 7's step table is archived in §3.

**Four of §4.11's eight requirements are already DONE** (increment 1,
confirmed against `060-IMPLEMENTATION-STATUS.md` rather than assumed):
`R-310-202` full retention, `R-310-203` draft discard on resolution,
`R-310-204` baseline-referenced versions undeletable, `R-310-205`
complete compressed documents and no deltas. This increment closes the
remaining four.

| Step | Scope | Requirements | Status |
|---|---|---|---|
| 8.1 | `baseline/models.py` — manifest, entry, content hash, gate refusal | `R-310-200` | **done** — 22 unit tests |
| 8.2 | `baseline/storage.py` + `repository.py` — `req_baselines`, write-once manifests | `R-310-200`, `204` | **done** — write-once, no delete API |
| 8.3 | `baseline/service.py` — the creation GATE and the manifest build | `R-310-201` | **done** — 20 integration tests |
| 8.4 | The working-draft iteration cap, reported as a run anomaly | `R-310-206` | **done** — 6 integration tests |
| 8.5 | `baseline/render.py` — DOCX + PDF from the manifest, base template | `R-310-207` | **done** — 21 unit tests, PDF verified by pypdf |
| 8.6 | `baseline/router.py` — REST + §13 catalogue rows | all + §13 | **done** — 5 routes, 19 integration tests |
| 8.7 | UI: the baseline panel and the review mutations deferred from increment 7 | `R-500-021` | **done** — baseline panel, 16 tests |
| 8.8 | Regenerate derived artefacts; both CI gates green | all | **done** — backend 3944 / UI 569, both gates green |

**A manifest NAMES content, it never copies it (`R-310-200`).** The
requirement says "SHALL NOT duplicate object content", and the reason is
not storage: a manifest holding its own copy of a paragraph creates a
second answer to "what did this baseline contain?", and the two will
disagree the first time one is migrated. The manifest therefore stores
object id, version and content hash, and reading a baseline resolves
each entry through `objects/storage.py` — which cannot delete a
referenced version (`R-310-204`).

**The gate of `R-310-201` collects every refusal, like the closure gate.**
Three preconditions, and a reviewer told only the first comes back twice
more.

**No new dependency.** `python-docx` and `pypdf` are already main
dependencies; checked against `pyproject.toml` before planning, as in
increment 4.

**Done-criterion for increment 8.** `run_tests.sh ci` All stages OK AND
`npm run ci` green, with every `R-310-200…207` carrying an
`@relation implements:` marker and `T-310-005` carrying a
`@relation validates:` marker.

---

## 3. Running log

> **Increments 1 to 4 are archived** in
> `.claude/sessions/2026-10-04-310-traceability-increments-1-4.md` — moved
> verbatim on 2026-10-04 when this file passed 1400 lines. The decisions
> they produced (`DV-01`…`DV-20`) stay in §5, which is the live index;
> only the narrative moved. §3 remains APPEND-ONLY for what follows.

### Step 4.6 — the intake REST surface (2026-10-01)

Nine routes under `/api/v1/projects/{project_id}/intake/`, catalogued in
`_catalog.py` (235 endpoints now) and registered in
`tests/coherence/test_route_catalog.py`. Ingest is `project_editor`;
`/verify` is `project_owner`, because confirming a degraded extraction is
the one permitted mutation of a supplied requirement (`R-310-061`) and
therefore belongs to whoever answers for the project, not to anyone who
can upload.

**A silently-ignored safety flag — the defect this step existed to catch.**
Two HTTP tests failed: an MD drop declaring `needed_ocr=true` reported
`source_class: structured` and was splittable. `extract()` accepted the
flag and forwarded it on the **PDF branch only**; MD, ReqIF, XLSX and
DOCX dropped it. Diagnosed **case A**, not a test defect, despite
`R-310-060`'s table naming PDF as the degraded case: the *reason* the
class exists is that OCR shifts character offsets, and that holds whatever
container the OCR output arrived in. A caller that declares OCR and is
silently ignored gets no `R-310-061` gate at all — the worst failure mode
available, since nothing surfaces. `needed_ocr` is now honoured by every
adapter; `degraded` is the conservative class, so over-applying it costs a
verification step and under-applying it costs a lost clause.

**A test defect alongside it (case B).** `_md_upload` returned
`dict[str, object]`, erasing both `files` and `data` types and forcing a
blanket `# type: ignore[arg-type]` on the call sites — mypy caught it in
`ci` after the targeted run was green. Replaced by a `NamedTuple`, which
removed the ignore. The ignore was the tell: a suppression with no
comment was hiding the fact that a malformed upload would have typechecked.

**Spec debt paid: `E-310-006` v2.** v1 listed `req_allocations` as an
**edge** collection. It is a document collection, and the reason is
structural: an edge needs a vertex at both ends, and an allocation's far
end is a *container* — a name in a published cycle, with no row to point
`_to` at. An edge would have forced a phantom vertex per container kept
in step with the cycle version defining it. The row is corrected, the
rationale recorded in the spec, and `req_object_locks` added to the table
with its no-backup note.

**Increment 4 closed.** `run_tests.sh ci` All stages OK — 3419 passed,
coverage 90.36 %. `065-TEST-MATRIX.md`, `backend-routes.json` and
`060-IMPLEMENTATION-STATUS.md` regenerated (235 endpoints, 475
requirements).

### Increment 5 — change absorption (2026-10-01)

`absorption/` inside C5: traversal, diff, models, repository, storage,
service, router. 9 routes, catalogue at 244 endpoints. CI green — 3593
passed, coverage 90.44 %.

**The traversal is two phases because reachability and path enumeration
have different costs.** Phase 1 expands each node at most once, so the
work is bounded by the subgraph and the round trips are the DAG's depth.
Phase 2 enumerates paths over the now-known edge set, which is
exponential in the worst case — so it is capped, the cap is *visible*
(`paths_truncated`, propagated to descendants), and the closure gate
depends on nodes and links only, never on path count. A capped path list
therefore cannot make a ticket wrongly closable. `causes` — the direct
reasons — stays complete always, because that is what `R-310-143`'s
rationale actually protects: a reviewer must not see half the reason.

**A cycle is named, not merely detected.** Kahn's algorithm establishes
that one exists; a DFS over the residual produces a concrete cycle,
because "there is a cycle somewhere" is not actionable when the fix is
to correct two specific links. An AQL traversal with `uniqueVertices`
would have deduped it silently, which is the reason the walk is not AQL.

**Three refusals I tightened beyond the letter of the spec**, each
recorded as a decision: a disposition carries its own evidence (DV-24), a
modification needs its gate to have passed first (DV-25), and a ticket
has no status field to write (DV-23).

**Five of my own defects, all caught before the gate.**

1. A **route-shadowing bug found by enumerating shapes**, not by a test:
   `GET /changes/impact/{requirement_id}` was swallowed by
   `GET /changes/{drop_id}/{requirement_id}` declared before it. Fixed
   structurally — own segment — rather than by reordering, since a future
   reorder would reintroduce it (DV-28).
2. **`ValidationError` unhandled on the disposition path** → 500 instead
   of 422. The *same* defect I fixed in `process/router.py` in increment
   2. Caught by the test that asserts a reason-less `confirmed-unchanged`
   is refused. Then `exc.errors()` embedded a datetime and would not
   serialise; aligned on `str(exc)` as `process/router.py` already did.
3. **`_open(change: object)` with `getattr` calls** — the same bogus-helper
   smell as `_drop_of` in increment 4. Typed as `SuppliedChange`.
4. **`CoverageLookup` lacked the drop id**, so fragments could not be
   found for a split requirement. Fixed the signature rather than working
   around it, and added `IntakeService.split_of`.
5. **Two wrong test expectations** (case B): a node count that included
   the seed, which the module excludes by design; and a cycle assertion
   pinning one rotation when the DFS start order picks another. The second
   was replaced by a *stronger* assertion — every consecutive pair must be
   a real edge and the walk must close — rather than a looser one.

**`test_no_parallel_definitions` fired a fourth time**, on `Disposition`
sharing `actor` / `at` / `justification` with `Allocation`. I measured
before acting, as in increment 4: across 84 registered contracts `actor`
appears in 5, `at` in 4, `justification` in **1** — `Allocation` alone,
against `project_id` 24 and `tenant_id` 12. The measurement does not
support promoting `justification` to a conventional field, so I did NOT
extend `CONVENTIONAL_FIELDS`; I registered the seven absorption contracts,
which §8.4 required anyway. Extending the exclusion list to silence a
finding I had not justified would have been gaming the guard.

**The traversal types moved to Pydantic (v2 of `traversal.py`)** for the
same reason: they are both the walk's output and the stored, exposed
shape, so a dataclass here plus a Pydantic twin in `models.py` would have
been precisely the parallel definition the check exists to catch.

**Also:** `req_changes` added to the C16 data map — backed up because a
*closed* ticket is the audit evidence that a supplied modification was
absorbed, not merely a rebuildable cache.

### Increment 6 — workflow execution & negotiated piloting (2026-10-01)

`execution/` inside C5 (models, estimation, budget, storage, repository,
service, router — 12 routes) plus `c3_conversation/object_tools.py`.
Catalogue at 257 endpoints. CI green — 3838 passed, coverage 90.47 %.

**`Q-310-008` answered deterministically, and the reason is not the
obvious one.** Auditability and cost are real but secondary. The binding
reason is that `R-310-174` requires a step to suspend when it EXCEEDS ITS
ESTIMATE — and an estimate a model re-rolls per run cannot be overrun in
any meaningful sense, because the guard would compare a measurement
against a number that already moved. A test pins reproducibility
explicitly rather than inferring it from the absence of randomness. No
seam was built for a model classifier: that would be speculative
structure (§2).

**Ratification is version-compared, not presence-checked.** `R-310-173`
lets a ratified plan be amended; `R-310-170` says work needs approval.
Read together, an amendment must REVOKE the approval, or amending becomes
the way to change terms already agreed. `is_ratified` therefore compares
`ratification.plan_version` against the plan's version, and `ratify`
echoes the version back so a plan amended between display and approval is
refused rather than approved unseen.

**Three refusals the spec does not spell out, each recorded as a
decision:** a gated step may not claim a duration (DV-29); an amendment
must re-cover exactly the scope it replaces (DV-30); an amendment
re-classifies rather than carrying the bad estimate forward (DV-31).

**C-05 ratified: `speculative` is a provenance marking, not a sixth
review state.** The operator chose the orthogonal reading. I then
implemented it as DERIVED rather than as the stored field the question's
preview showed, and flagged the deviation: `suspect_links` in the same
module already computes the adjacent property with the comment "a stored
flag would drift the moment a target changed without it being updated".
The same argument applies exactly — nothing writes to the covering object
when its upstream is accepted, so a column would survive its own reason.
`R-310-177` is amended to v2 with the clarification; `R-310-006` keeps its
five states.

**The agent tool catalogue's omissions are tested by name.** The review
and lock routes DO exist on C5 (`/review`, `/lock`, `/lock/force`), so
withholding them is a real restriction rather than an artefact of nothing
being implemented — which is why a by-name assertion is worth its line. A
forbidden call is refused with a REASON, because a model told only
"unknown tool" tries a synonym.

**Four of my own defects.**

1. **I invented the C5 object paths** (`/objects/containers/{c}` where C5
   serves `/containers/{c}/objects`) and only caught it by enumerating the
   live router. A mocked test would have passed forever. The test now
   asserts every client URL against the live route shapes.
2. **`create_app` passed ruff's statement ceiling** (PLR0915, 59 > 50).
   Extracted `_wire_traceability` rather than suppressed — five
   sub-surfaces with six injected lookups had made the composition root
   unreadable (§1.3 flagged).
3. **`_SEVERITY` keyed by strings** while indexed with enum members: it
   worked only because `StrEnum` hashes like its string. Keyed by the enum.
4. **A catalogue row inherited the wrong method** from the line above it
   during a scripted insertion — caught by `test_route_catalog`.

**An observation, not acted on:** my route-shape check compares
`(method, shape-with-wildcards)` and therefore does NOT catch
literal-vs-variable shadowing — the increment 5 bug class. It happened to
be safe here (`/speculative` and `/suspect` sit at a depth with no
variable route), but the check is weaker than I assumed when I wrote it.
Logged as IMP-05.

### Increment 7 — the workbench UI (2026-10-02)

First increment in `ay_platform_ui/`. `lib/workbenchTypes.ts`,
10 client methods, 5 regions under `components/workbench/`, one route.
UI `npm run ci` green — 553 tests, all four thresholds met (statements
83.2, branches 72.1, functions 83.3, lines 86.6 against 80/70/80/80).
Backend `run_tests.sh ci` still green, 3847 passed.

**IMP-05 paid first, and the detector was VERIFIED rather than assumed.**
`tests/coherence/test_route_shadowing.py` refuses a literal route that an
earlier variable route absorbs — the increment-5 bug class, which the
ad-hoc "shape" check could not see because a literal and a variable
produce different shapes. I then reconstructed the original bug in a
scratch router and confirmed the detector fires on it, naming the
absorbing route and the segment. A check nobody has seen fail is a check
nobody should trust.

**`R-500-019` is enforced by a type, not by a convention.** "Every
coverage figure shows its auto-accepted share" is honoured in the first
component and forgotten in the fourth. So `CoverageFigure` makes
`autoAccepted` non-optional and ONE component renders every figure: a
caller holding only a total does not typecheck. The share renders even at
zero, because its absence would make a fully-reviewed figure
indistinguishable from a component that dropped the breakdown.

**The no-navigation requirement is asserted against a router spy.** jsdom
has no address bar, so a test that only checked "the objects changed"
would pass against a page that navigates. `useRouter` is mocked and
`push`/`replace` are asserted un-called after a container switch.

**Two of my own defects.**

1. **`use(params)` in a client component.** That is the server-component
   idiom; it SUSPENDS, and with no boundary above it the entire surface
   rendered an empty `<div/>` — all 11 page tests failed with nothing in
   the DOM. Every other page here uses `useParams`, so mine was the lone
   deviation. Diagnosed case A and fixed the page, not the tests.
2. **The client's first endpoint paths were invented**, as in increment 6
   — caught immediately this time because `api-surface.test.ts` drives
   every method through a spy against the router-pinned snapshot, and
   because I read the 54 C5 paths out of `backend-routes.json` before
   writing a line. That guard also enforces completeness: the ten new
   methods failed the build until each was wired into the contract test.

**A scope boundary stated rather than left to be found.** The workbench
delivers the READING and review-SURFACING loop. Review mutations,
allocation and plan ratification are rendered as affordances (R-500-021
requires the prompt at the point of reading) but not wired; that is
increment 8's scope beside baseline export. Recorded in the page header
so the next reader does not mistake it for an omission.

**`500-SPEC-UI-UX.md` v7** adds `R-500-015`…`R-500-021`, closing the
amendment row `310-SPEC` §8.2 had open since the spec was written. The
§8.2 row for `100-SPEC-ARCHITECTURE.md` was closed too, as **no
amendment needed**: it asked to extend a "collection ownership table"
that does not exist — `R-100-012` v3 assigns ownership generically, and
enumerating collections there would create a second list able to drift
from `E-310-006`.

### Increment 8 — baseline & rendering (2026-10-04)

`baseline/` inside C5 (models, storage, repository, service, render,
router — 5 routes), the `R-310-206` iteration cap in `objects/service.py`,
and the baseline panel in the workbench. Catalogue at 262 endpoints.
Backend `run_tests.sh ci` All stages OK — 3944 passed, coverage 90.46 %.
UI `npm run ci` green — 569 tests, all four thresholds met.

**CORRECTION TO MY OWN CLAIM, made before anyone relied on it.** I first
headed this entry "310-SPEC COMPLETE". That was wrong. The eight planned
increments are done; the SPEC is not. Auditing
`060-IMPLEMENTATION-STATUS.md` rather than trusting the board showed 22
of 97 requirements still `not-yet`. Seven of those were mine to fix on
the spot — `R-310-220`…`226` were built in increment 7 but carried only
their `R-500-*` mirrors as markers, so the traceability chain did not
reach them; markers added, they are now `implemented`. **Fifteen remain
genuinely unbuilt** and are scheduled as increment 9 in §1. "All the
increments are done" and "the spec is satisfied" are different
statements, and the board was letting me conflate them.

**Four of §4.11's eight requirements were ALREADY DONE**, confirmed
against `060-IMPLEMENTATION-STATUS.md` rather than assumed: `R-310-202`,
`203`, `204`, `205` landed with increment 1. Checking before building
saved re-implementing retention that already existed.

**THE PDF IS HAND-WRITTEN, AND THAT IS A FLAGGED RISK.** `pypdf` READS
PDFs; it does not lay one out. `reportlab` / `fpdf2` are not dependencies
and adding one is forbidden without an explicit request (§5.2). So
`render.py` writes a minimal single-font PDF with the standard library —
Helvetica, a base-14 font needing no embedding.

The risk is mitigated by VERIFICATION, not confidence: every test
round-trips the bytes through `pypdf` and asserts the text extracts, and
the 200-object test proves the xref offsets survive pagination — the one
thing a hand-rolled PDF gets wrong silently. A real parser accepting the
file is the only evidence worth having. **If a PDF library is ever
permitted, `render_pdf` is one function to replace.**

**A hash bug I would have shipped.** `content_hash` first took only the
text. `R-310-008` makes prose and figures exclusive — a figure carries
`notation`, not `body` — so hashing `body` alone would have hashed the
EMPTY STRING for every figure, collapsing all of them to one hash. Found
by reading `DocObject`'s real fields instead of assuming a `content`
attribute. The kind is now folded into the digest and two tests pin it.

**The same assumption had already leaked into the UI.** `DocObjectSummary`
declared `content: string`, which the backend never sends — and the
contract test could not see it, because it pins PATHS, not payload shapes.
Corrected to mirror `DocObjectPublic` exactly (`body` / `notation`
nullable and exclusive, `produced_by` a workflow reference), which then
surfaced three more drifted call sites through the typechecker. Logged as
IMP-06: the contract guard has a real blind spot.

**A genuine name collision, and the newcomer yielded.**
`test_no_parallel_definitions` reported `ObjectEntry` shadowing C7's
MinIO listing entry of the same name. Renamed to `ManifestObject` /
`ManifestLink`, which also match the manifest's own `objects` / `links`
fields.

**And a parallel-definition finding I argued against rather than
registered reflexively.** `ManifestLink` shares six fields with
`CoverageLink`. They stay distinct because a manifest is an IMMUTABLE
HISTORICAL RECORD: if it embedded the live model, a field added to
`CoverageLink` would change how baselines taken years earlier
deserialise — precisely what `R-310-204` protects. Recorded as DV-38.

**Two design defects caught by mypy, both mine.** `CycleLookup` omitted
the `tenant_id` that `resolve_cycle` requires — it could not have resolved
anything — and it ignored that the call returns `None` for a project with
no published cycle, which is now an explicit 409 rather than an
`AttributeError`. `ContainerLookup` was typed on the storage shape
(`DocObject`) where the service layer returns `DocObjectPublic`.

**`/baseline-readiness` sits on its own first-level segment**, applying
the increment-5 lesson BEFORE `test_route_shadowing` had to catch it.

**Three env tiers touched** (§4.6): `C5_DRAFT_ITERATION_CAP=12` added to
`.env.example` and `tests/.env.test` through the Edit tool, a
non-semantic addition with no decision gate.

---

## 4. Clarifications needed from the operator

| # | Question | Blocks | Status |
|---|---|---|---|
| C-01 | `Q-310-001` — notation for `figure` objects. | **Mermaid only in v1**; PlantUML behind the same adapter interface when a diagram kind demands it. | step 1.2 | **RATIFIED 2026-09-30** |
| C-02 | `Q-310-004` — which role publishes a cycle / a workflow. | **`tenant_admin` publishes, `project_owner` tailors.** No new role in v1: adding a method-engineer role would touch `E-100-002`, the §13 route catalogue and the whole isolation matrix. | increment 2 | **RATIFIED 2026-09-30** |
| C-03 | Lock lease duration and force-release policy. | **15 min**, renewed on activity, force-releasable by the container owner, configurable via `C5_OBJECT_LOCK_LEASE_SECONDS`. | step 1.4 | **RATIFIED 2026-09-30** |

| C-04 | **Does the object model REPLACE the DocGen live-docs tool loop, or sit alongside it?** `D-023`'s consequence clause (my text) said "SHALL be replaced". Reading the code showed that overreached — see below. | **Sit alongside.** Object tools for the cycle surface; `create_document` / `update_document` (live-docs, D-015, `R-200-153..156`) untouched. | step 1.7 | **RATIFIED 2026-09-30** — `D-023` amended in `999-SYNTHESIS` v11 |

Ratified questions stay listed with their answer: the reasoning is the
part a later session needs, and re-deriving it costs more than reading it.

### C-04 in detail

`c3_conversation/document_tools.py` exposes `list_documents` /
`read_document` / `create_document` / `update_document` /
`delete_document`, which call C4's live-docs CRUD. That surface is
**free-form project documents** governed by `200-SPEC` (D-015,
`R-200-150..156`), deliberately shaped to match the future MCP catalogue.

The 310 object model is a **different surface**: documents that belong to
a published cycle container, carry allocated requirements, and move
through a review lifecycle. Nothing in `310-SPEC` asks for live-docs to
disappear — §1 "Out of scope" does not mention them, and no `R-310-*`
requires it.

So the consequence clause I wrote in `D-023` is wrong in the direction of
too much change. Replacing live-docs would force every free-form note and
draft through a cycle container and a review gate, which serves none of
the use cases live-docs exists for, and would be a regression justified
by nothing in the spec.

### C-05 — is `speculative` a sixth review state, or a provenance marking? (raised 2026-10-01, gates step 6.7)

`R-310-006` declares a **closed** set of five review states: `proposed`,
`accepted`, `auto-accepted`, `stale`, `rejected`. `R-310-177` requires
that an object covering a non-accepted upstream "SHALL be marked
`speculative`". Taken as a review state, that is a sixth member and the
two requirements contradict each other.

**My reading, and why.** `speculative` is a marking about **provenance** —
what the object was built on — while a review state is about
**examination**. They are orthogonal, and under end-to-end execution they
have to be: a reviewer accepts the architecture object while the
functional object above it is still `proposed`, so an object must be able
to be `accepted` AND speculative at once. A sixth enum member would make
that state unrepresentable and would force a choice between recording
"reviewed" and recording "built on sand".

The second half of `R-310-177` supports this too: "SHALL become `stale`
if that upstream object changes" is the existing staleness mechanism
(`CoverageLink.is_stale`, pinned version vs current), not a new one.

**What I need.** Either confirmation of the orthogonal reading — in which
case `R-310-006` needs no amendment and `R-310-177` gains a clarifying
sentence — or a decision to amend `R-310-006` to six states, in which case
`ReviewState` changes and that is a §8.4 contract change (case D) touching
increment 1's model, its storage and its REST surface.

*(New questions are raised before the step they gate, per the operator's
instruction to be proactive.)*

---

## 5. Decisions taken during dev

| # | Decision | Why | Spec impact |
|---|---|---|---|
| DV-01 | The object sub-package lives at `c5_requirements/objects/` rather than as a new component. | `R-310-001`/`002` extend the C5 corpus model; the collections are C5-owned per `R-100-012`. A new component would split ownership of one corpus across two services. | none |
| DV-02 | `R-310-008` (figure exclusivity) and `R-310-010` (version advances only on a decision) are enforced in the Pydantic models, not only in the service. | A service-level-only check leaves the invariant bypassable by any future caller, including the reindex path that rebuilds objects from MinIO. | none |
| DV-03 | **Operator instruction (2026-09-30): no new pod / micro-service unless genuinely necessary.** Ratifies DV-01 and binds every later increment: increments 2–8 extend C5, C4, C6 and the existing UI tier. No new `COMPONENT_MODULE` is introduced by 310. | One image × N containers (`R-100-114` v2) is the platform's shape; a traceability service would add a pod, a route, a catalog section and an isolation surface for a corpus C5 already owns. | none |
| DV-04 | `DocObject` (the MinIO storage shape) is **not** registered as a contract until a real cross-component consumer exists. | `§8.4` registers *exposed* interfaces. Declaring `C6_validation` as a consumer before C6 imports anything would make the registry assert a relationship that does not exist. | none |
| DV-05 | `ReviewDecision` carries `auto-accept` as a distinct member from `accept`. | `R-310-007` requires `auto-accepted` to be distinct in storage and on the wire; deriving it from a flag on `accept` would let a serialiser collapse the two. | none |
| DV-06 | `RequirementsStorage.put_document` gained a `content_type` parameter (default unchanged). | The method claimed to write "a document" but baked `text/markdown`. Parameterising it lets the object store write JSON and gzip through the same primitive instead of duplicating the MinIO plumbing. Backwards compatible; flagged as an adjacent change per `CLAUDE.md` §1.3. | `minio_storage.py` v1 → v2 |
| DV-07 | `WorkingDraft` gained a `container` field. | Found while writing `put_draft`: the draft could not locate its own object. A draft must be self-locating exactly as `DocObject` is. | none |
| DV-08 | `ObjectStorage` exposes **no** operation to delete or compact a version, and a unit test asserts that absence. | `R-310-202`/`R-310-204` become a structural guarantee rather than a runtime policy. An API that can destroy history will eventually be called. | none |
| DV-09 | Increment 1 creates `req_objects` only. `req_object_edges` of `E-310-006` is **not** created yet. | Nothing holds an edge until the coverage graph of increment 3; the parent/child link is a field. `CLAUDE.md` §2 forbids speculative structure. | none — `E-310-006` lists the target state |
| DV-10 | A review decision other than rejection **always** creates a version, including `confirm-unchanged` on unchanged content. | The version chain is the decision ledger. `R-310-150` makes "examined and unaffected" evidence, and evidence must be addressable. Cost is a few KB against the order-of-magnitude headroom of `R-310-303`. | none |
| DV-11 | `confirm-unchanged` is refused while a working draft is unresolved. | Asserting "no change is needed" over a pending rework is a false statement, and it would silently discard the rework. | none |
| DV-12 | Object lock rows are **excluded** from C16 backup; the object index **is** backed up. | Restoring live leases would block edits for holders absent from the restored project. Backing up the index follows the `req_entities` precedent so a restore-as-new is usable without a reindex pass. | `data_map.py` |
| DV-13 | `ReviewState.REJECTED` is defined but unused in increment 1. | Rejecting a *redraft* leaves the object at its accepted version; rejecting an *object outright* is a review-queue action that arrives with increment 3. Inventing its semantics now would be speculation (§8.1). | none |
| DV-14 | A stale `expected_version` maps to **409**, a foreign lease to **423 Locked** — not 400 and not 403. | The request was well-formed and the caller is entitled to write; what changed is the world. The payloads carry both version numbers / the holder and expiry, so the UI can offer a diff or say who to ask instead of showing a dead end. | none |
| DV-15 | The scope guard (`validate_scope`) lives at the **service** boundary, not only in storage. | A read served entirely from the derived index never touches MinIO, so a storage-only guard let a malformed container slug answer 200 with an empty list instead of being refused. | none |
| DV-16 | Project tailoring **replaces** the tenant definition; there is no field-level merge. | `R-310-046` allows disable / restrict / override. A merge produces a definition nobody authored and nobody reviewed, and makes "what does this project actually run on?" unanswerable without re-deriving it. Replacement plus a mandatory rationale is auditable. | none |
| DV-17 | A version slot holds the draft, then the sealed definition. `seal` refuses only an **already-approved** occupant. | Promotion in place is the lifecycle, not a violation. The first implementation refused any occupant and broke publication outright. | none |
| DV-18 | Returning an allocation is gated on `project_owner`, not on a container owner. | `R-310-068` says "the owner of the container", but per-container ownership is modelled nowhere. `project_owner` withholds a right the spec might grant rather than granting one it withholds — the safe direction. Tightens, never loosens, when ownership lands. | none yet; revisit if container ownership is introduced |
| DV-19 | REST request bodies live in each package's `models.py`, never in `router.py`. | A request body necessarily restates its contract's domain fields; co-locating them makes that reviewable side by side. Putting them in the router also trips `check_no_parallel_definitions` legitimately, since the router module is not canonical. | none |
| DV-21 | `diff.py` takes the previous set as an **argument**, not from a baseline lookup. | `R-310-140` says "the current baseline", and baselines land in increment 8. Today the service feeds it the stored supplied requirements; later it feeds a baseline manifest. The diff logic does not change either way, so the seam costs nothing and avoids blocking increment 5 on increment 8. | none |
| DV-22 | A requirement whose text differs **only in whitespace** opens no ticket; comparison is on whitespace-normalised text, and the normaliser is a parameter. | An issuer's re-export commonly reflows every paragraph. Byte comparison would open thousands of identical tickets, and a flood of tickets is not the conservative outcome: nothing in it gets reviewed properly. Normalisation cannot hide an added, removed or reordered token — a test pins that — and the reflowed ids are reported rather than dropped. Pass `identity` for byte comparison where whitespace is load-bearing. | none |
| DV-23 | `ChangeTicket` has **no** `status` field; status is a property derived from `closed_at`. Nor is there `assignee`, `priority` or `due_date`, anywhere in the module. | `R-310-149` forbids exposing manual status mutation. The strongest form of that is the absence of the thing to set, not a guarded setter — a stored `status` string is exactly what someone later updates directly. Tests assert the absence on the model, on the index row and on the router. | none |
| DV-24 | A `modified-and-accepted` disposition cannot be constructed without the object version it produced; a `confirmed-unchanged` one cannot carry a version at all. | "Modified" is a claim about an artifact and the version is the observation behind it — the platform's standing rule that gate evidence is observed, never inferred (`R-200-012`). The symmetric refusal keeps a "nothing happened" record from asserting a product. | none |
| DV-25 | `confirmed-unchanged` needs **no** prior gated qualification; `modified-and-accepted` does. | `R-310-148` requires the gate before any *rework*. Deciding nothing needs doing IS the human gate; demanding a second one is ceremony, not control. A recorded modification, by contrast, is evidence rework happened — so its gate must already have passed. | none |
| DV-26 | A failed re-run of the `R-310-092` exhaustiveness check reports as `uncovered-requirement`, not as a fourth blocker kind. | `R-310-146` names three blockers. A clause falling outside every fragment is a clause answered by nobody, which is what that blocker means. A fourth kind would be a spec amendment (§8.1), not an implementation choice. | none |
| DV-27 | The batched reverse edge lookup (`links_to_targets`) lives in `coverage/repository.py`, not in `absorption/repository.py`, and is named for what it reads rather than what the caller does with it. | `req_object_edges` has one owner. Two AQL writers against one collection is how query semantics drift. Inverting a stored link into a propagation step is change absorption's claim, so that inversion stays in the absorption service. | none |
| DV-28 | `absorb` and `impact` sit on their own first-level path segments rather than under `/changes/`. | `GET /changes/impact/{requirement_id}` beside `GET /changes/{drop_id}/{requirement_id}` made correctness depend on declaration order — the literal route silently shadowed, with `drop_id="impact"`. Caught by enumerating route shapes before writing the tests. A test now pins that the preview reaches its own handler. | none |
| DV-29 | A `step-by-step` step SHALL have a zero duration estimate. | `E-310-004` says so, and the reason is that a gated step's duration is set by reviewer availability, which the platform cannot observe. A number there would be invented, and an invented figure in a ratification is worse than an absent one. | none |
| DV-30 | An amendment's partition SHALL re-cover exactly the scope it replaces. | A lost unit drops work the plan accounted for; an added one smuggles in work nobody priced. Both defeat the point of having ratified an estimate. | none |
| DV-31 | An amendment RE-CLASSIFIES the units it re-splits rather than carrying the original effort class forward. | The reason to amend is that the classification was wrong (`R-310-173`'s own rationale). Carrying it forward would preserve the bad estimate the amendment exists to fix. | none |
| DV-32 | `speculative` is DERIVED from the graph on every read, not stored on the object. | Ratifies C-05's orthogonal reading and extends it: nothing writes to the covering object when its upstream is accepted, so a stored flag would outlive its own reason. Same argument the module already applies to `suspect_links` and `CoverageLink.is_stale`. Deviates from the stored shape sketched in the question; flagged to the operator. | `R-310-177` v2 |
| DV-33 | An unknown review state is NOT treated as unaccepted. | A supplied requirement has no review state, so treating unknown as unaccepted would mark every object answering a customer requirement speculative — that is every object in the first container. Loud and useless beats quiet and useful only when the loud signal is actionable. | none |
| DV-34 | The workbench types live in `lib/workbenchTypes.ts`, not in `lib/types.ts`. | That file is 1385 lines across six components; a seventh surface makes both harder to read, and these types are consumed by one page tree. Flagged per §1.3 rather than done silently. | none |
| DV-35 | `CoverageFigure.autoAccepted` is REQUIRED, and one component renders every figure. | `R-500-019` is a property of every figure in the UI. An optional field is a field callers omit, and a convention a reviewer must re-check on each new panel decays. A type error is the only version that holds. | none |
| DV-36 | `useSelection` THROWS outside its provider instead of returning an empty default. | A region rendered outside the workbench with a silent empty selection would look like it works and share nothing — per-panel state, which `R-500-016` forbids, reintroduced invisibly. | none |
| DV-37 | An unknown uncovered-count renders as NOTHING, not as zero. | "0 outstanding" before the question has been asked is a figure a reader acts on wrongly. Absence is honest; zero is a claim. | none |
| DV-38 | `ManifestObject` / `ManifestLink` stay distinct from `DocObjectPublic` / `CoverageLink` despite heavy field overlap. | A manifest is an immutable historical record (`R-310-204`). Embedding the live models would mean a future field on `CoverageLink` changes how baselines taken years earlier deserialise — exactly what baselining prevents. `CoverageLink.is_stale(current)` also asks about NOW, which is meaningless on a frozen pin. | none |
| DV-39 | The PDF is written by hand with the standard library, not by a library. | `pypdf` reads PDFs and cannot lay one out; adding `reportlab`/`fpdf2` is forbidden without an explicit request (§5.2). Bounded by using a base-14 font (no embedding) and mitigated by round-tripping every test output through `pypdf`. One function to replace if a library is ever permitted. | none |
| DV-40 | `content_hash` folds the KIND (`body` / `notation`) into the digest. | `R-310-008` makes prose and figures exclusive, so hashing the text alone hashed the empty string for every figure and collided all of them. Found by reading `DocObject`'s fields rather than assuming a `content` attribute. | none |
| DV-41 | A baseline includes `accepted` and `auto-accepted` objects and EXCLUDES `proposed`. | `R-310-201` does not say so in words, but a `proposed` object is content nobody agreed to; including it would make the photograph a record of what an agent wrote rather than of what the project decided. `auto-accepted` IS acceptance (`R-310-007`) and the state is recorded per entry so an audit still separates them. | none |
| DV-42 | A rendering is cached beside the manifest but is never authoritative; `?refresh=true` re-renders. | The manifest is the record, a DOCX is a projection — so a stale rendering is never a correctness problem, while a template change should be visible without taking a new baseline. | none |

---

## 6. Improvements noticed, not made

Logged rather than silently fixed: each is outside the increment that
found it, and changing a platform-wide mechanism mid-increment would
bury the change in an unrelated diff.

| # | Observation | Why it matters |
|---|---|---|
| IMP-01 | `tests/coherence/test_route_catalog.py` discovers routers from a **hand-maintained `_ROUTERS` list**. A new router omitted from it escapes the §13.2 guarantee entirely — as mine did until I added it. | The check advertises "no route escapes the catalog" but delivers "no route from a registered router escapes the catalog". Walking the component app factories would close it. |
| IMP-02 | `tests/coherence/test_functional_coverage.py` matches **path literals** in test sources, so a test using composed URLs reads as absent coverage. | It produces false negatives that pressure contributors to restructure working tests. Resolving simple f-string concatenations, or matching on the router path constant, would remove the pressure. |
| IMP-03 | `scripts/` carries 23 pre-existing ruff findings, outside the CI's `src tests` scope. | Not a defect today — but it means `ruff check .` cannot be used as a local pre-flight without noise. |
| ~~IMP-04~~ | **FIXED 2026-09-30** (operator-requested). `check_no_parallel_definitions` v3 subtracts conventional scoping and audit columns before counting overlap. | See the log entry; detection power on domain fields is unchanged and the exclusion list is pinned by test. |
| ~~IMP-05~~ | **FIXED 2026-10-02.** `tests/coherence/test_route_shadowing.py` now refuses a literal route an earlier variable route absorbs, and the detector was verified against a reconstruction of the real increment-5 bug. Original observation: the ad-hoc route-shape check I use before writing router tests compares `(method, shape-with-wildcards)`, so it does NOT catch literal-vs-variable shadowing — the exact bug class of increment 5, where `/changes/impact/{id}` was swallowed by `/changes/{drop}/{req}`. It caught nothing in increment 6 only because `/speculative` sits at a depth with no variable sibling. | A **coherence test** should assert, platform-wide, that no literal path segment is reachable only behind a variable route declared before it. That is a real check the whole platform benefits from, and building it mid-increment would bury it in an unrelated diff. Candidate for the start of increment 7. |
| IMP-06 | `api-surface.test.ts` pins the PATHS the UI client calls, not the SHAPES it expects. `DocObjectSummary` declared `content: string` where the backend sends `body` / `notation`, and the guard was blind to it — the drift was found only by reading `DocObjectPublic` while writing the baseline hash. | A **payload-shape** check would close it: generate a JSON-schema snapshot per exposed Pydantic contract and assert the TS interfaces match, the same chain the route snapshot already uses. Real work, and a separate increment: it needs a generator, a snapshot format and a drift test. The 405-class bug this platform already paid for was a path; this is its payload twin. |
| IMP-07 | **The composition root is exercised by nothing.** Proven by mutation: hard-wiring `is_covered` to `True` in `_wire_traceability` — which in production closes a change ticket whose requirement is answered by nothing (`R-310-146`) and lets a baseline be taken with an unanswered rated requirement (`R-310-201`) — leaves **503 C5 integration tests green**. Every integration test substitutes its own double for exactly those six lookups. | The slice tests are sound (13/13 mutations caught at service level); what no test crosses is the WIRING. Needs one integration test that builds the real `create_app` against testcontainers and drives a request through the real lookups. Highest-value single test the repo could gain. |
| IMP-08 | **Router mounting is not pinned to the app.** Proven by mutation: deleting `app.include_router(...)` for baseline, absorption and execution — 26 endpoints, the whole HTTP surface of three increments — leaves **2687 tests green**. `test_route_catalog` compares the catalogue to the ROUTER OBJECTS, never to what `create_app` mounts. | A one-line fix to an existing check: walk `create_app().routes` instead of (or as well as) the `_ROUTERS` list. Cheap, and it closes a class where the code is right, the catalogue agrees, the tests pass and the running service serves 404. |
| IMP-09 | **The error-mapping branches of every router are the least-covered code in the 310 surface** (routers sit at 74–89 % while services sit at 97–99 %), and the uncovered lines are precisely the `except ... -> 404/409/422/423` clauses that ARE the user-facing contract. Not hypothetical: this exact bug shipped TWICE (`ValidationError` → 500 instead of 422, in `process/router.py` then `absorption/router.py`). | Not 50 hand-written error tests. A PARAMETRISED check over the §13 catalogue: for each route, assert each known service error maps to its documented status. Real design work — increment-9 material. |
| IMP-10 | **Five repository methods have zero call sites**: `BaselineRepository.list_baselines` / `baseline_count` / `drop_baseline`, `ExecutionRepository.list_suspended`, `AbsorptionRepository.count_open`. The baseline listing reads MinIO and fetches one manifest PER TAG — an N+1 — while a single AQL query over the index it maintains sits unused. `list_suspended` carries a docstring arguing it is needed so a suspended step is not lost among active plans; I then never exposed it. | Decide per method: wire it (the baseline listing should read its index — that is both the N+1 fix and what every other 310 surface does) or delete it (§11.2 #8 — but only after confirming it is genuinely dead). Writing an index nothing reads is the worst of both. |
| IMP-11 | **No e2e test touches the 310 surface.** The e2e tier holds three named journeys (golden path, backup restore, cross-tenant refusal) and none walks the traceability loop: ingest a supplied requirement → allocate → author an object → cover it → absorb a change → close the ticket → baseline → export. Eight increments, ~1500 tests, and the user's actual journey is unproven end to end. | One e2e journey, which would also close IMP-07 as a side effect. The pieces all exist and are individually tested; what is missing is the proof they compose. |

