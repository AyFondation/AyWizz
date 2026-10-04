<!-- =============================================================================
File: 2026-10-04-310-traceability-increments-1-4.md
Version: 1
Path: .claude/sessions/2026-10-04-310-traceability-increments-1-4.md
Description: Archived running log of 310-SPEC-DOC-TRACEABILITY increments 1 to 4
             (object model, cycle/workflow catalogue, coverage graph, intake and
             splitting), moved out of `.claude/DEV-PLAN-310.md` §3 on 2026-10-04
             when that file passed 1400 lines. The entries are VERBATIM: this is
             an archive, not a summary, because the value of the trail is the
             reasoning and the recorded mistakes, both of which a precis would
             lose.

             What the four increments established, in one line each:
               1. Objects are the storage grain; versions are decision-triggered
                  and never compacted; locks are leases.
               2. The cycle and the workflow are versioned, publishable DATA,
                  and project tailoring REPLACES rather than merges.
               3. Coverage is a conclusion computed from allocations, never a
                  settable flag; a return escalates on the second occurrence.
               4. A supplied requirement is immutable, its anchor re-verified on
                  every read, and a split cannot be obtained without the rework
                  request owed to its issuer.

             The decisions these increments produced (DV-01..DV-20) stay in the
             plan's §5, which is the live index; only the narrative log moved.
============================================================================= -->

# Archive — 310 traceability, increments 1 to 4 (running log)

> Moved verbatim from `.claude/DEV-PLAN-310.md` §3. Increments 5 to 8 remain
> in the plan; they move here when the plan next outgrows itself.

**2026-09-30 — increment 1 opened.** Spec `310` v1 written, `D-023`…`D-027`
landed in the synthesis, `R-300-080` amended to v2 and `Q-300-002`
resolved. Read the existing C5 patterns before writing anything:
`models.py` (StrEnum + Pydantic v2 + `field_validator`),
`storage/minio_storage.py` (async facade over the sync `minio` client via
`asyncio.to_thread`, static path helpers, `StorageError`),
`db/repository.py` (async wrapper serialised by an `asyncio.Lock` because
python-arango's `Database` is not thread-safe; `add_index({'type': …})`
because the per-type helpers raise warnings that pytest promotes to
errors). The new sub-package follows all three.

**2026-09-30 — step 1.1 done.** `objects/models.py` v1. The
figure/prose exclusivity of `R-310-008` and the decision-triggered
versioning of `R-310-010` are enforced as model validators rather than
left to the service layer, so no caller can construct an object that
violates them.

**2026-09-30 — two coherence failures, both instructive.**

*(a) `test_no_parallel_definitions` fired* on `DocObject` /
`DocObjectPublic` sharing audit columns (`created_at`, `created_by`,
`version`, `project_id`) with `EntityPublic`. Reading
`scripts/checks/check_no_parallel_definitions.py` rather than guessing:
the checker skips modules that already hold a **registered** contract.
So the fix was not to loosen the threshold but to do the `§8.4`
registration I owed and had deferred to step 1.6. Diagnosis §10.3 case A
(implementation defect — missing registration), not a check defect.

*(b) I registered `DocObject` with `consumers=("C6_validation",)` — and
no such consumer exists.* That is a fabricated relationship in the
registry, the same class of error as asserting unobserved gate evidence
(`R-200-012`). Corrected: `DocObject` is the MinIO storage shape with no
cross-component consumer today and is deliberately **not** registered;
the module is already canonical via `DocObjectPublic`, so nothing was
needed. A contract test now asserts its absence, with the reason, so a
future session does not "helpfully" re-add it.

Consequence: `test_schemas.py::TestContractRegistration.EXPECTED` had to
grow by three names. That is §10.3 **case D** (intentional contract
change), flagged as such — the inventory test did exactly its job of
refusing an unnoticed surface growth. Semantics before: C5 exposes four
REST contracts. After: seven, the three new ones being the object-grain
REST surface. No assertion was weakened; `test_all_contracts_use_rest_transport`
still holds unchanged because the three additions are all REST.

**2026-09-30 — step 1.1 verified.** `./scripts/run_tests.sh ci` →
**All stages OK** (ruff OK, mypy OK, pytest OK). **2720 passed**,
coverage **89.78 %** (was 89.46 % before this step, so the new module is
covered above the corpus average). Report:
`reports/2026-09-30_0939_ci/`. One tautological assertion
(`AUTO_ACCEPTED != ACCEPTED`) was caught by mypy as a non-overlapping
comparison and replaced by a real check on the serialised wire values —
per §11.2 anti-pattern #5 the right move was to delete the tautology,
not to suppress the diagnostic.

**2026-09-30 — step 1.2 done.** `objects/storage.py` v1. Three path
families with three lifetimes: the current object
(`objects/<oid>.json`, overwritten), the retained version chain
(`objects/_versions/<oid>@vN.json.gz`, append-only), the working draft
(`objects/<oid>.draft.json`, overwritten then removed). `put_object`
writes the snapshot **before** the current object: a current object
without its retained version violates `R-310-202`, whereas a snapshot
without a current object is an orphan the reindex ignores.

Two defects found by writing the code rather than by a test:
`WorkingDraft` had no `container` and could not locate its own object
(DV-07); and `put_document` baked `text/markdown` while claiming to
write "a document" (DV-06).

One test earns its integration tier explicitly:
`test_list_object_ids_filters_the_versions_subtree`. MinIO's recursive
prefix listing returns the `_versions/` sub-tree under the same prefix as
the current objects, so an implementation that forgot to filter it would
**pass against a naive in-memory double and fail against the real
backend**. The unit tier keeps a double for the other cases; this one is
only meaningful against MinIO.

**2026-09-30 — step 1.2 verified.** `./scripts/run_tests.sh ci` →
**All stages OK**. **2759 passed**, coverage **89.86 %** (89.78 % after
step 1.1). Report: `reports/2026-09-30_1002_ci/`.

First run failed on ruff (`RUF100`, an unused `noqa: SLF001` in the new
integration test). Cause: the targeted `ruff check <paths>` run before
the gate did not include that file. **Lesson for later steps — lint the
tree, not the touched paths**; the targeted run is for fast iteration
only, never for the closing check (`CLAUDE.md` §12.1 already says this
about pytest; it applies identically to ruff). **Correction the next run
produced**: the CI scope is `ruff check src tests`, not `.` — `scripts/`
carries 23 pre-existing findings that are out of scope. Match the
wrapper's invocation, do not invent a stricter one.

**2026-09-30 — steps 1.3, 1.4, 1.5 done.** `./scripts/run_tests.sh ci` →
**All stages OK**. **2833 passed**, coverage **90.00 %** (89.86 % after
step 1.2). Report: `reports/2026-09-30_1036_ci/`.

*1.3 — the index.* `req_objects` only. `req_object_edges` is NOT created:
nothing holds an edge yet, the parent/child relation is a field, and
`CLAUDE.md` §2 forbids speculative structure. It arrives with the
coverage edges of increment 3 (DV-09).

*1.4 — the lease.* Held in Arango, not MinIO, because acquisition needs
an atomic compare-and-set. One AQL `UPSERT` takes the whole decision, so
8 concurrent contenders yield exactly one holder — asserted.

The AQL failed first with `syntax error, unexpected RETURN`. Cause, once
read rather than guessed: `IN` is **also AQL's array-membership
operator**, so `… ? @doc : {} IN req_object_locks` parsed as a membership
test and the UPSERT silently lost its target collection. Parenthesising
the whole conditional fixes it. Worth a learned rule if it recurs.

Expiry is decided **in code, never by the TTL index**: Arango's collector
is periodic, so a lapsed lease would keep blocking an edit for an
unpredictable window. A test asserts the row is still physically present
while `get()` already returns None.

*1.5 — the rule, demonstrated.* `test_negotiation_then_one_decision_yields_exactly_one_version`
is the test increment 1 exists for: four draft iterations, then one
accept, and the version chain goes `[1]` → `[1, 2]`.

Semantics settled while writing it, all inside the spec:
- rejection creates no version and leaves the object untouched — a
  refused draft never became content;
- `confirm-unchanged` DOES create a content-identical version, because
  `R-310-150` makes "examined at v6, found unaffected" evidence, and
  evidence must be addressable;
- `confirm-unchanged` is **refused while a draft is unresolved**:
  asserting "no change needed" over a pending rework would be a lie;
- creation yields `proposed` v1 with no review record — an agent's
  proposal is not yet a decision.

**2026-09-30 — three coherence failures, all my omissions.** The
platform's cross-cutting checks caught what the component tests could
not:

- `test_backup_data_map` — new collections escaped the C16 DataMap. Per
  `D-022` a project-scoped store that escapes backup is a defect.
  Classified following the existing precedent: `req_objects` is **backed
  up** (like `req_entities`, itself derived per `R-300-013`, so that a
  restore-as-new is usable without a reindex pass); `req_object_locks` is
  **excluded** — restoring live leases would block edits on behalf of
  holders that do not exist in the new project.
- `test_env_completeness` ×2 — `C5_OBJECT_LOCK_LEASE_SECONDS` was added
  to the Settings but not to `.env.example` / `.env.test`. Added through
  the Edit tool per §4.6 (Tier 1, non-semantic: a new variable at its
  default, no decision gate).

All three are §10.3 **case A** — implementation defects, fixed at the
root. No check was relaxed.

**2026-09-30 — step 1.6 done.** `./scripts/run_tests.sh ci` →
**All stages OK**. **2893 passed**, coverage **89.89 %**. Report:
`reports/2026-09-30_1120_ci/`. Eleven routes, mounted in `main.py`
alongside the existing C5 router — same process, same bucket, same
database, **no new pod** (DV-03).

Two transport decisions are contract, not incidental, and are asserted
at the HTTP tier:
- a stale `expected_version` is **409** carrying BOTH version numbers, so
  a UI can offer a diff instead of just failing (`R-310-192`);
- a foreign lease is **423 Locked** naming its holder and expiry, not
  403: a lease is a temporary state, not an authorisation verdict, and
  the reviewer needs to know who to ask (`R-310-193`).

**A real gap found in the §13 guarantee.** `CLAUDE.md` §13.2 promises
that a route present in code but absent from `_catalog.py` fails the
build. It does not, in general: `tests/coherence/test_route_catalog.py`
enumerates routers from a **hand-maintained `_ROUTERS` list**, so my 11
routes passed unnoticed until I added the router to that list myself.
The check is only as complete as a list someone remembered to update.
Registering the router restored the guarantee for this surface; making
discovery automatic (walk the app factories instead of a literal list)
is a separate improvement, logged as **IMP-01** below.

**Two further coherence gates fired, both legitimate:**
- `test_functional_coverage` — 9 of 11 endpoints looked untested. They
  were not: the check scans test sources for **path literals**, and my
  URLs were composed f-strings, invisible to it. Fixed by writing full
  literals (and shortening the ids so each fits the 100-char budget —
  the catalog matches those segments as placeholders, so their value is
  free). Not a workaround: the check asks for legible evidence that a
  path is exercised, and the fix makes the evidence legible. Two missing
  smoke tests surfaced in the process (`GET …/versions/{version}` and
  `GET …/draft`) and were written.
- `test_ui_contract_snapshot` — `ay_platform_ui/tests/contract/backend-routes.json`
  drifted. Regenerated via the documented script; `065-TEST-MATRIX.md`
  likewise (185 → 196 endpoints).

**One implementation defect the HTTP tier caught that no lower tier
could.** `GET …/containers/.hidden/objects` returned **200 with an empty
list**: the path guard lived in the storage layer, and a read served
entirely from the derived index never reached it. Fixed by lifting the
guard to the service boundary (`validate_scope`), so it binds on every
path rather than only those that touch MinIO. §10.3 case A.

The first version of that test asserted 400 on `..%2Fescape` and failed
with 404 — **case B, a test defect**: Starlette decodes `%2F` before
routing, so the input never reaches the validator and the test was
measuring Starlette. Split into two: a malformed single-segment slug
must be 400, and an encoded separator must simply never resolve
(400 or 404, never 200).

**2026-09-30 — increment 1 CLOSED.** `./scripts/run_tests.sh ci` →
**All stages OK** (`reports/2026-09-30_1203_ci/`). Six steps delivered,
two closed without code.

The two closures are the part worth remembering, because both reversed a
line of the plan rather than executing it:

- **1.7** — the "DocGen migration" I had billed as the increment's
  principal cost does not exist. Opening
  `c3_conversation/document_tools.py` showed DocGen drives **live-docs**
  (D-015, `R-200-150..156`): free-form project documents, a different
  surface from cycle containers. `310-SPEC` never asks for their removal.
  My own consequence clause in `D-023` was wrong in the direction of too
  much change; it is amended (`999-SYNTHESIS` v11) from "replaced" to
  "alongside". What survives is a second tool catalogue, moved to
  increment 6 where its caller lives.
- **1.8** — `100-SPEC` has no enumerated collection table to amend;
  `R-100-012` v3 is generic and already covers the new collections.

Both were settled by reading the artefact rather than trusting the plan.
A plan written before the code is read will contain items like these, and
executing them because they are written is how a diff acquires changes
nobody needed.

---

**2026-09-30 — increment 2 opened, step 2.1 done.** `process/models.py`
v1, 42 unit tests. Three invariants are structural rather than left to
the service, following the increment-1 pattern:

- an **approved** workflow with an empty `checks` list cannot be
  constructed (`R-310-041`) — that is the line between an engineering
  activity and a named prompt. A *draft* without checks is allowed: the
  gate binds at publication, not while the author is still writing;
- **control flow cannot express a loop** (`R-310-043`). A return target
  exists only on a human-gate and must name an EARLIER step; a forward or
  self return is rejected. There is no `condition` / `when` / `loop` /
  `variables` field at all, and a test asserts their absence — the
  schema, not a convention, is what stops a workflow language from
  becoming an unauditable scheduler;
- a **tailored entity without a rationale** cannot be constructed
  (`R-310-046`), nor a rationale without a tailoring target.

Two smaller guards earn their place: a container `scope` must be a
substantive statement (≥ 20 chars), because `R-310-066` makes it the text
an allocation cites to justify itself and a document owner cites to
refuse one — a token scope makes both citations meaningless; and
`scope_id`s must be unique within a cycle, since a duplicate would make
that citation ambiguous.

Two tests failed first, both my fixture's fault (§10.3 case B): removing
`checks` while leaving a step that references one trips the referential
rule before the publication gate. Fixed by isolating the gate with a
check-free step set — the model was right.

**2026-09-30 — step 2.1 verified.** `./scripts/run_tests.sh ci` →
**All stages OK**. **2935 passed**, coverage **90.02 %**. Report:
`reports/2026-09-30_1304_ci/`.

`test_no_parallel_definitions` fired again, for the same structural
reason as in increment 1: a module holding no **registered** contract is
not canonical, so its models are compared against every other contract
and match on audit columns. Registered `CyclePublic` and `WorkflowPublic`
— and this time with `consumers=("C1_gateway",)` **only**. C4 will read a
published workflow when the runner lands (`R-310-025`, increment 6) and
is added then. The registry states relationships that exist, not ones
that are planned; that was exactly the `DocObject` error earlier today,
and repeating it would have been worse than making it once.

Noted as **IMP-04**: the check effectively requires a contract to be
registered the moment its module exists, which pushes registration ahead
of the route that serves it. Harmless here — 2.4 serves these in the same
increment — but it is why the registration precedes the router.

**2026-09-30 — step 2.2 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **2979 passed**, coverage **90.11 %**. Report:
`reports/2026-09-30_1442_ci/`.

**Immutability is in the shape of the API, not in a flag.** `ProcessStorage`
exposes `put_draft` (overwrites, refuses anything not a draft, refuses to
overwrite a sealed slot) and `seal` (write-once, refuses an occupied
slot, refuses anything not approved). There is **no third method** able
to replace a sealed version, and a test asserts that no
delete/remove/replace/unseal method exists at all. The reason is
concrete: every object carries `produced_by: WF-nnn@vN`, and a rewritable
published version turns that stamp into a lie.

Each version is its own MinIO object. Unlike document objects there is no
"current" file, because *current* is a question about which version is
**approved** — and that is a scope-dependent answer the index gives.

**"Superseded" is an index statement, not a rewrite.** Publishing v2
transitions the v1 INDEX row to `superseded`; the sealed v1 document
keeps the status it was published with. Asserted at the integration tier
against real ArangoDB, alongside tenant/project scope isolation: a
project tailoring shares the entity id with the tenant catalogue and
never sees it.

`next_version` is derived from the index rather than tracked as a
counter — a counter would be a second source of truth able to drift from
what is actually stored.

Two fixes on the way through: a ruff `UP047` (PEP 695 type parameters
instead of a `TypeVar`), and one of my own assertions that named the
wrong field — I asserted `"scope" not in row` meaning "no container scope
TEXT", while `row["scope"]` is the `t:acme` filter key I deliberately put
there. Case B; rewritten to assert the scope prose is absent from the
serialised row.

**The C16 DataMap gate fired again**, for `req_cycles` / `req_workflows`.
Both backed up, following the `req_objects` precedent: a restore-as-new
without its cycle and workflow catalogue has documents but no process to
produce them by. Third time this gate has caught a new collection — it is
earning its place.

**2026-09-30 — step 2.3 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **2999 passed**, coverage **90.14 %**. Report:
`reports/2026-09-30_1512_ci/`.

**A real design flaw, caught by the integration tier — 11 tests red at
once.** I had made the draft and the sealed definition share a version
path, so `seal` refused the very draft it was meant to promote: it
rejected ANY occupant rather than an already-APPROVED one. The invariant
was misstated in code. What is immutable is a version **that is already
published**; a draft is not published, and promoting it in place is the
normal lifecycle.

Fixed by parsing the stored status instead of testing for an occupant —
and the same fix removed a latent bug in `put_draft`, which matched the
byte string `"approved"` inside the raw JSON. A container scope statement
may legitimately contain the word "approved" (supplier agreements,
variant matrices), and a publication guard that prose can defeat is not a
guard. Both paths now parse.

**My unit suite had let this through.** No unit test covered
draft → seal promotion; the design flaw only surfaced against real
storage. Two unit tests added: promotion in place, and a scope statement
containing the word "approved" not defeating the guard. That gap is the
more useful finding — the integration tier caught it, but it should not
have had to.

The service itself now carries the two behaviours increment 2 exists for:
editing a published version is refused with the remedy in the message
("create a new draft version instead"), and `resolve_*` answers "what
applies to THIS project?" — the project's published tailoring when it has
one, the tenant catalogue otherwise. **Tailoring replaces, it never
merges** (DV-16): a field-level merge between a tenant cycle and a
project override yields a definition nobody authored and nobody can
review, whereas replacement plus a mandatory rationale is auditable.
Asserted: a project that drops the security container from its tailoring
does not get it back from the tenant catalogue, and a tenant publishing
v2 does not silently change what the project runs on.

A draft tailoring deliberately does NOT win resolution — only a published
one does. Work in progress must not change what a project runs on.

**2026-09-30 — step 2.4 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3093 passed**, coverage **90.06 %**. Report:
`reports/2026-09-30_1533_ci/`. Twenty routes, symmetric across the two
entity kinds and the two scopes, mounted in `main.py` beside the object
surface — still one component, still no new pod.

C-02 is now executable rather than a note: `tenant_admin` writes the
catalogue, `project_owner` writes tailorings, and the HTTP tier asserts
that **neither can do the other's job** — a `project_owner` gets 403 on
the tenant catalogue, a `tenant_admin` gets 403 on a project tailoring.
Reads stay merely authenticated: everyone working under a process needs
to see it, or the allocation citations of `R-310-066` become
unverifiable.

`.../resolved` is the endpoint every consumer actually wants, and its
404 message says why nothing applies — *"neither a project tailoring nor
a tenant catalogue entry"* — rather than an unexplained empty answer.

**Two real defects the HTTP tier caught, both mine, both case A:**

- `ValidationError` was unhandled on the **create** paths, so a tailoring
  with no rationale (`R-310-046`) and a workflow whose control flow loops
  (`R-310-043`) produced **500** instead of 422. The update and publish
  paths already mapped it; creation did not. Fixed by routing all four
  create handlers through shared `_create_cycle` / `_create_workflow`
  bodies that map it.
- `status.HTTP_422_UNPROCESSABLE_ENTITY` is deprecated in this Starlette
  version and pytest promotes the warning to an error. Replaced by
  `HTTP_422_UNPROCESSABLE_CONTENT`.

The 20 catalog rows were **generated**, not typed: twenty near-identical
entries are exactly where a hand-written diff acquires a silent
transposition, and the route-catalog coherence check only verifies that
paths match, not that a role gate was copied correctly from the row
above. `065-TEST-MATRIX.md` and the UI route snapshot regenerated
(196 → 216 endpoints).

**2026-09-30 — step 2.5 done; increment 2 CLOSED.**
`./scripts/run_tests.sh ci` → **All stages OK**. **3105 passed**,
coverage **90.08 %**. Report: `reports/2026-09-30_1627_ci/`.

`R-310-025` says the workflow is bound "to that container's phase **in the
active cycle version**", so the binding lives on `ContainerSpec` — the
cycle is the only thing that knows about phases. It is **optional**: a
cycle may legitimately declare a container nobody automates. The
precondition binds when an activity STARTS, not when a cycle is
published.

`resolve_activity` is the precondition expressed as a function a caller
must pass through — holding the `ActivityBinding` it returns IS the
evidence the precondition is met, and the resolved workflow travels with
it so a runner cannot fetch it again and land on a different version.

**Every refusal carries a machine-readable reason** — `no-published-cycle`,
`unknown-container`, `no-workflow-bound`, `workflow-not-published`. A
refusal a user cannot act on is as useless as no refusal: the workbench
must be able to say what is missing, and a message string cannot be
branched on. A test asserts **every** reason is reachable, so the enum
stays pinned to behaviour rather than to intent.

Resolution goes through the project's lens end to end (`R-310-022`): a
project that tailored its authoring workflow runs its own version, and a
project whose cycle tailoring binds a container the tenant left unbound
gets its activity from its own process.

**Two failures, both my own test selectors, both case B:**
- `test_object_endpoints` selected object rows with `"/containers/" in
  path` — the new activity route also lives under a `{container}` segment
  and was swept in as a "stale object row". Narrowed to `"/objects"`.
- the activity URL was an implicit concatenation across two lines, so the
  functional-coverage matcher saw neither half. Shortened the fixture ids
  so one literal fits the line budget. **Second time this bites** — worth
  a learned rule if it recurs (see IMP-02).

**2026-09-30 — increment 3 opened, step 3.1 done.**
`coverage/models.py` v1, 30 unit tests.

`ReviewState` is **imported from the object model, not redefined**: an
allocation and a coverage link move through the same proposed → accepted
lifecycle as an object, and a parallel enum would be two vocabularies for
one idea — which `check_no_parallel_definitions` exists to catch.

Four invariants are structural:

- **A requirement allocated to three containers and answered in two is NOT
  covered** (`R-310-064`). `is_partial` is a distinct, nameable state, so a
  partial gap can be reported as what it is instead of rounding to green.
- **A split requirement is covered through its fragments and never through
  its own allocations** (`R-310-096`). Holding both is refused outright:
  it would be two answers to one question.
- **Weak coverage never counts as coverage** (`R-310-122`). An allocation
  whose only objects are weak is uncovered. Folding weak into covered is
  precisely what turns a matrix green and untrustworthy.
- **Staleness is computed against the target's current version, never
  stored** (`R-310-145`), and a test asserts no `stale` field exists. A
  stored flag drifts the moment the target moves without it being updated.

`is_covered` is a property, not a field, and a test asserts no settable
`covered` exists: a conclusion that can be set can disagree with its own
evidence.

Two smaller ones worth the lines: an allocation must **justify** itself,
not merely cite a `scope_id` (a citation with no reasoning cannot be
reviewed), and `out-of-project` is a **verdict carrying a justification**
rather than a silence — the failure `R-310-065` closes is an allocating
agent quietly omitting what it cannot place.

**2026-09-30 — step 3.1 verified.** `./scripts/run_tests.sh ci` →
**All stages OK**. **3135 passed**, coverage **90.15 %**. Report:
`reports/2026-09-30_1644_ci/`.

`test_no_parallel_definitions` fired a **third** time, same structural
cause (IMP-04): a module with no registered contract is not canonical.
Registered `Allocation`, `CoverageLink`, `RequirementCoverage`,
`ContainerCoverage` — `consumers=("C1_gateway",)` only, again. C6 will
consume coverage findings when the audit activity lands and is added
then. Three increments, three times the same ordering friction; IMP-04 is
worth fixing rather than re-absorbing.

**2026-09-30 — IMP-04 fixed (operator-requested).**
`./scripts/run_tests.sh ci` → **All stages OK**. **3144 passed**,
coverage **89.59 %**. Report: `reports/2026-09-30_1719_ci/`.

**The diagnosis, measured rather than assumed.** Counting fields across
the whole registry: `project_id` appears in 20 registered contracts,
`tenant_id` in 11, `version` in 9, `created_at` in 8, `updated_at` in 7.
These are scoping and audit columns the platform attaches *structurally*
to every persisted entity. With a threshold of 3, any two persisted
models collide on them alone — so the overlap signal was drowning in
guaranteed noise, and every new model module failed the build until one
of its models was registered as a contract.

**The fix** (`check_no_parallel_definitions` v3): subtract
`CONVENTIONAL_FIELDS` — the seven structural columns — before counting.
A flag now requires **three shared DOMAIN fields**, which is the actual
copy-paste signal.

**This is a precision fix, not a weakening**, and the tests say so rather
than the commit message:
- `test_a_genuine_duplicate_is_still_flagged` — a class re-declaring
  `entity_id / type / status / category / title / body` is still caught;
- `test_audit_only_overlap_is_not_significant` — the real `DocObject` vs
  `EntityPublic` field sets, the case that fired three times, now overlap
  on `{type, body}` and fall under the threshold;
- `test_the_exclusion_list_is_exactly_this` — the list is **pinned
  exhaustively**. A list of "fields that don't count" is exactly what
  gets quietly extended to silence a real finding (§11.2 #3), so
  extending it must break a test and become a deliberate act.

The threshold itself is untouched; only the noise was removed.

**One finding on my own work along the way.** I had put
`@relation validates:R-100-012` on the new checker test, and
`test_relation_markers` correctly refused it: the test cannot reach any
code implementing R-100-012, because it validates a **coherence tool**,
not a requirement. The §8.4 no-parallel-definition rule is a `CLAUDE.md`
discipline, not an `R-` entity. Marker removed with the reason recorded —
inventing a requirement to satisfy a marker check is the fabrication
those checks exist to prevent.

**Consequence for the registrations made under the old pressure:** none
are reverted. `CyclePublic`, `WorkflowPublic`, `Allocation`,
`CoverageLink`, `RequirementCoverage`, `ContainerCoverage` are genuine
contracts their routers serve or will serve in the same increment; only
the *pressure* to register them early is gone.

**2026-09-30 — step 3.2 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3169 passed**, coverage **90.22 %**. Report:
`reports/2026-09-30_1749_ci/`.

**Two shapes, deliberately different, and both asserted.**
`req_object_edges` is a real EDGE collection — the impact DAG of §4.8 has
to traverse it — and carries `_from` / `_to` handles alongside flat
`object_id` / `target_id` fields so the coverage matrix can aggregate
without every requirement vertex existing yet (intake materialises those
in increment 4). `req_allocations` is a DOCUMENT collection, and a test
asserts `edge is False`: an edge needs two vertex documents and a
*container* is not one — it is declared by the cycle. The spec entity
`E-310-006` says "edge" and is wrong; the amendment is owed (§2).

**Returns accumulate, they do not replace.** `R-310-069` escalates on the
SECOND return of an allocation, which is only countable if the first
survives — so each return is its own row, ordinal-keyed. And dropping an
allocation deliberately **leaves its return history intact**: the returns
are what the next allocation reads (`R-310-068`), so erasing them with
the allocation would make the replay uninformed, which is the whole
failure that rule exists to prevent.

**The unallocated audit asks the CANDIDATE set**, not the stored rows.
`R-310-065` closes a failure of *silence* — an allocating agent quietly
omitting what it cannot place — so the question has to be "of the
requirements that exist, which were never decided about?". A query over
what is stored could never see an omission. A test also pins that a
*return* alone does not count as decided: a returned requirement is back
in limbo and must keep surfacing until it gets an allocation or a verdict.

All three allocation decisions — allocation, verdict, return — share one
collection, because "what was decided about where this requirement is
answered" is one question and splitting it would make the audit a join.

**2026-09-30 — step 3.3 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3197 passed**, coverage **90.28 %**. Report:
`reports/2026-09-30_1801_ci/`.

**A serious bug, caught by the last test written.**
`container_coverage` passed the container's WHOLE link set to the
classifier, which filtered by container but not by target. So a
requirement with no coverage of its own was reported **covered** by an
object answering a different requirement in the same container. That is
the exact failure mode the whole increment exists to prevent — a matrix
that reads green on evidence that belongs to something else.

Fixed by moving the target filter INSIDE the classifier, so neither
caller can forget it. `requirement_coverage` was already correct because
it pre-filtered; correctness that depends on every caller remembering is
not correctness.

**The rules, and what each is guarding:**

- `R-310-066` — an allocation citing a **neighbour's** scope is refused as
  firmly as one citing none. A format check alone would pass it, and that
  is precisely how an agent allocating by lexical similarity goes wrong:
  a requirement mentioning a bus protocol lands in the technical design
  when it is an architecture or a security requirement.
- `R-310-067` — criticality propagates by default; a lower level is
  permitted **only** with a recorded decomposition rationale. A tool that
  refused every decomposition would contradict ISO 26262-9 and be worked
  around, which is worse than not enforcing at all.
- `R-310-069` — a container that returned a requirement is excluded from
  the next allocation, the **second** return escalates, and a human may
  still override deliberately. The override exists because a blanket
  exclusion with no exit is the kind of rule people route around.
- `R-310-121` — covering an unallocated target is refused: an object
  cannot answer something nobody asked it.
- `R-310-145` — a pin against a target with **no** current version is
  refused outright. Such a link could never go stale, so it would be a
  permanent false green.

Weak and stale coverage both leave an allocation **uncovered**, and the
container view reports uncovered / weak / stale **separately** — the
workbench asks "what do I fix first?", and collapsing them makes that
unanswerable.

The current-version lookup is **injected**, not reached for: requirement
versions live outside this module, and a callable keeps the dependency
explicit and the rules testable without standing up a requirements corpus.

**2026-09-30 — step 3.4 done; increment 3 CLOSED.**
`./scripts/run_tests.sh ci` → **All stages OK**. **3237 passed**,
coverage **90.28 %**. Report: `reports/2026-09-30_1824_ci/`. Nine routes,
mounted alongside the object and process surfaces; 226 endpoints
catalogued.

**Refusals carry the rule, not just a status.** A wrong scope citation is
409 whose message names `R-310-066` AND the scope the container actually
declares; covering an unallocated target names `R-310-121`; a blocked
re-allocation names `R-310-069`. The workbench has to be able to
*explain*, and a bare 409 explains nothing.

**The IMP-04 fix proved itself on its first real test.**
`test_no_parallel_definitions` fired on `AllocateRequest` (6 shared
fields) and `CoverRequest` (4) — all **domain** fields, none conventional.
So the noise reduction did not blind it, and the finding was correct: I
had put the request bodies in `router.py`, while increments 1 and 2 put
them in `models.py` beside the contracts they serve. Moved, following the
established layout. Co-location is what makes a request body's necessary
restatement of its contract reviewable side by side instead of drifting
in another file.

**One honest compromise, recorded rather than papered over (DV-18).**
`R-310-068` reserves returning an allocation to "the owner of the
container", but **per-container ownership is modelled nowhere** in the
platform. `project_owner` is the conservative stand-in: it never grants a
right the spec withholds, only withholds one it might eventually grant.
When container ownership is introduced, the gate tightens rather than
loosens — the safe direction to be wrong in.

**2026-10-01 — increment 4 opened, step 4.1 done and verified.**
`./scripts/run_tests.sh ci` → **All stages OK**. **3264 passed**,
coverage **90.31 %**. Report: `reports/2026-10-01_0600_ci/`.

`R-310-092` is the one guard that prevents a clause being lost when a
supplied requirement is split, and the failure it catches is **invisible**:
a split that drops half a sentence leaves every surviving fragment
covered, so the matrix reads green and nothing ever points at the missing
text. Expressed as interval arithmetic it needs no model and no reviewer
to be right.

**I reversed my own design here, and the tests are what forced it.**

The first implementation read "in full" loosely: a gap was permitted when
it contained no alphanumeric character, so the ", " between two clauses
could belong to neither fragment. I recorded that as an interpretation and
wrote it up as DV-20. The **first realistic sentence killed it** — the
separator between the last two clauses is `", and "`, which contains a
word. Exempting conjunctions would have meant a stop-word list, i.e.
precisely the judgement the check exists to avoid.

The convention that dissolves the problem instead: **a fragment INCLUDES
its separators**, so the intervals are contiguous and "in full" can be
read literally, with no interpretation at all. The interval is the audit
artefact — it proves nothing was dropped; the fragment's readable
statement is a separate field, trimmed for display. They do not have to be
the same string.

DV-20 is therefore **not** recorded as a decision: it was a workaround for
a problem a better convention removed. What is pinned instead is a test
asserting that even a two-character `", "` gap is refused — because once
"insignificant gap" exists as a notion, somebody has to decide what
qualifies, and that decision is where a lost clause hides.

Two details that earn their lines: intervals are **half-open**, so
adjacency is `a.end == b.start` rather than a difference of one, which is
where off-by-one gaps come from; and the report carries the **evidence**
(which text was dropped, which fragments collide) rather than a boolean,
because an author cannot act on "invalid".

**2026-10-01 — step 4.2 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3297 passed**, coverage **90.39 %**. Report:
`reports/2026-10-01_0744_ci/`.

Five invariants are structural rather than left to the service:
an unresolvable anchor cannot be constructed (`R-310-062`); a finding
citing no declared criterion cannot be constructed (`R-310-063`); a
fragment identifier is **derived** from parent and ordinal, so the
issuer's numbering cannot be imitated (`R-310-095`); a split proposal must
carry the **failed atomicity finding** that justifies it and must tile the
source (`R-310-092`, `R-310-093`); and a degraded source reports that it
needs human verification before anything is done to it (`R-310-061`).

`excluded_from_issuer_export` is a **property returning True**, not a
stored flag, with a test asserting no such field exists. Exporting
fragments to a customer under numbering resembling its own is the
contractual confusion `R-310-095` forbids, and a flag can be set to False.

**The pinning test worked — on me.** `test_no_parallel_definitions` fired
on `QualityFinding` sharing `requirement_id` / `actor` / `at` with
`Allocation`. My first instinct was that `actor` and `at` are the 310
packages' attribution pair, structurally equivalent to
`created_by` / `created_at`, and belong in `CONVENTIONAL_FIELDS`.

I measured instead of assuming, as the IMP-04 fix itself had done:
`actor` appears in **3** registered contracts and `at` in **2**, against
`created_at` in 8 and `project_id` in 20. That is not a convention — it is
a small cluster from a single increment, all of it mine. **Extending the
exclusion list was not justified by the data, and the exhaustive pinning
test is what forced me to check rather than to extend.**

The correct fix was the same as in increments 2 and 3: register the
module's contracts. `SuppliedRequirement`, `QualityFinding`, `Fragment`,
`SplitProposal` and `ReworkRequest` are genuinely the intake REST
contracts, served by step 4.6 in this same increment — `C1_gateway` only,
as before.

**2026-10-01 — step 4.3 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3333 passed**, coverage **90.37 %**. Report:
`reports/2026-10-01_0804_ci/`. Five formats, no new dependency: ReqIF is
XML and XLSX is a zip of XML (stdlib both), `python-docx` and `pypdf` were
already main deps.

**The structural guarantee of this step.** Every adapter returns the
document text it extracted PLUS spans into it, and `ExtractedRecord`
refuses to exist unless its text IS the slice its interval names — an
`Extraction` re-verifies every record on construction. An adapter cannot
therefore report an anchor that does not resolve, which turns `R-310-062`
into a fact about the data rather than a promise about the code. An
adapter miscomputing offsets would otherwise ship silently broken
provenance, and provenance is the first thing a contract review asks for.

The document text is **stored**, not just read: offsets index into
something the platform keeps, so an anchor stays resolvable after the
original file is gone.

**An implementation ambiguity, resolved as a default and labelled as one
(§8.1).** "What counts as a requirement" in prose formats has no spec
answer. Resolved here as: a paragraph is a candidate when it carries an
RFC-2119 modal. The modal set is a **parameter**, not a constant, and the
intake quality review (`R-310-063`) is what catches both the noise it
admits and the requirements it misses. ReqIF deliberately bypasses the
heuristic: a SPEC-OBJECT is a requirement by declaration, so filtering it
on prose would discard what the issuer explicitly marked.

**Tests build real files**, not mocks: a real DOCX through python-docx, a
real XLSX through stdlib zip, real ReqIF XML. The offsets are the thing
under test, and a mock would simply agree with whatever the code did. PDF
is the exception — page texts are injected, because `read_pdf` is a
three-line boundary around the library and authoring a text PDF to test
it would test pypdf rather than the paragraph logic.

`needed_ocr` is the only input that overrides a format's declared class:
OCR shifts character offsets, so `R-310-092` would pass against corrupted
text and lose a clause invisibly (`R-310-061`).

**2026-10-01 — step 4.4 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3355 passed**, coverage **90.38 %**. Report:
`reports/2026-10-01_0854_ci/`.

**What this step actually adds.** Step 4.3 made an anchor resolvable at
extraction time; this one makes it **stay** resolvable. The extracted
document text is stored beside the requirements and `get_requirement`
re-verifies the anchor against it on every read, verification being the
default rather than an option. An anchor that only resolved while the
original file sat on somebody's disk is not provenance. The original
payload is kept too: an extraction is OUR reading of what the customer
sent, and a contract review asks about the thing itself.

**A supplied requirement is never rewritten** (`R-310-090`). Storing one
with different text is refused, and the message names the remedy: a
changed requirement arrives as a **new drop**, detected by diffing drops
(`R-310-140`), not by mutating the old one. The single permitted update is
the human verification of a degraded source, which confirms our extraction
rather than changing what the issuer wrote.

**Two of my own mistakes, both caught rather than shipped:**

- I wrote a helper that reached for `proposal.drop_id` on a model that had
  no such field, wrapped in enough `getattr`/`or` to look plausible. The
  field genuinely belonged on `SplitProposal` — a split is of a requirement
  received in a specific drop — so the model gained it and the helper was
  deleted. Convoluted plumbing around a missing field is a signal that the
  model is wrong, not that the plumbing needs more cleverness.
- Adding that field broke the step-4.2 fixture, and my targeted test runs
  did not include it. **Same class of mistake as the ruff lesson already in
  this log**: run the suite, not the touched file. Targeted runs are for
  iteration; they are not a closing check, even for pytest.

One test failure was instructive about layering: changing a requirement's
text without its anchor tripped the MODEL's anchor-length guard
(`R-310-062`) before the storage immutability guard (`R-310-090`) could
fire. The substitute now has to be internally consistent, so the test
exercises the rule it names.

**2026-10-01 — step 4.5 done and verified.** `./scripts/run_tests.sh ci`
→ **All stages OK**. **3375 passed**, coverage **90.41 %**. Report:
`reports/2026-10-01_0916_ci/`.

**`R-310-094` is enforced by the return type.** `propose_split` returns
the **pair** — the proposal AND the rework request — so a caller cannot
obtain the split without the message to the issuer. Correcting a supplied
defect only internally absorbs the issuer's debt silently and leaves
nothing to show at a contract review; making the two inseparable in the
signature is stronger than documenting that they should both happen.

**`R-310-093` is tightened beyond the model.** The model requires a split
to carry a failed atomicity finding; the service additionally requires
that finding to have been **recorded**, so a caller cannot manufacture its
own justification inside the request. A test asserts that a *different*
criterion (an unbound term, say) does not authorise a split either.

**The degraded gate lives in the service, not at the route**, because it
must hold however the split was requested — chat, agent, or REST.

Two smaller decisions worth their lines: the **issuer's own identifier
wins** whenever it is usable, because a contract review speaks in the
customer's numbering and not ours; and a derived id is **deterministic**
from the drop, so a re-ingest can be compared instead of duplicated. A
finding is keyed by criterion, so a later verdict replaces the earlier one
— a criterion either fails or it does not, and accumulating verdicts
would leave a reviewer asking which is current.

Recording a finding reads the requirement first on purpose: a finding
about something never received is noise, and the read also re-verifies the
anchor the finding will be quoted against.

Three of my own slips, all caught by tests rather than shipped: four
storage methods referenced before they existed (added); an async
generator used where an iterator was needed; and a `str` return that mypy
saw through.
