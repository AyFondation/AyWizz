<!-- =============================================================================
File: 2026-10-12-ui-api-chain-and-openapi-docs.md
Version: 1
Path: .claude/sessions/2026-10-12-ui-api-chain-and-openapi-docs.md
Description: Two audits built as reusable Python tools rather than as LLM
             reading passes, on the operator's explicit instruction to
             "pre-chew" the work with scripts. The first closed the one
             unguarded link of the UI→API chain — response and then request
             SHAPE — and found seven live defect groups including a page that
             threw `undefined.length` in production with its test green. The
             second made the generated OpenAPI document say what the platform
             actually does: 268 of 272 operations declared no 4xx, and 555
             parameters advertised the gateway-injected identity headers as
             caller-supplied, i.e. the document instructed readers to forge
             what `forward-auth-c2` exists to strip. Both audits caught
             defects in THEMSELVES — seven times between them — and the
             pattern is the lesson: an audit that cannot read one side of its
             comparison reports zero drift, which is indistinguishable from
             agreement.
============================================================================= -->

# Session — UI↔API chain + generated OpenAPI docs (2026-10-07 → 2026-10-12)

## Context

The operator asked how to verify, end to end, each API the UI consumes —
and explicitly preferred reusable Python tooling over an LLM reading pass:
*"ne pas forcément tout faire directement via l'analyse du LLM, et
pré-mâcher une partie du travail avec des scripts python qui pourraient
être réutilisables."* That framing shaped everything below. Where a
property was derivable, a script derives it; where it was not, the script
reports the gap rather than guessing, and the gap is counted.

---

## Part 1 — The UI→API chain

### The survey came first, and it changed the plan

Five of the six links were ALREADY guarded, each by its own check:

| Link | Owner |
|---|---|
| apiClient → route exists | `ay_platform_ui/tests/contract/api-surface.test.ts` |
| catalogue → live FastAPI routers | `tests/coherence/test_route_catalog.py` |
| catalogue → UI JSON snapshot | `tests/coherence/test_ui_contract_snapshot.py` |
| catalogue → Traefik + `forward-auth-c2` | `tests/coherence/test_gateway_route_coverage.py` |
| endpoint → a test exists | `scripts/checks/audit_functional_coverage.py` |

The gateway link is covered for UI calls **transitively** — every emitted
path is pinned to the catalogue, every catalogued path to its winning
router — so re-asserting it per call would have duplicated a stronger
check with a weaker one. The sixth link, **response shape**, had nothing:
`grep -rn openapi tests/ scripts/` was empty.

### The join key was already in the source, unused

```
this.request<ProjectList>("/api/v1/projects", { method: "GET" })
             ^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^
             the UI's view  (method, path) → the backend's OpenAPI schema
```

Backend truth comes from importing each component's **production**
`main.py` (`app = create_app()` is module-level, `lifespan` does not run on
import), so there is no router/prefix table to maintain and the prefixes
are the deployed ones.

### Seven live defect groups

All verified against the Pydantic models (`extra="forbid"`) before being
called defects:

1. `FindingPage.findings/limit/offset` → C6 serves `items`/`run_id`/`total`.
   `findings.length` threw on `undefined`: **the validation run page
   crashed** for every completed run.
2. `ValidationRun.total_findings` → C6 serves `findings_count` by severity.
   The literal string `"undefined"` was rendered.
3. `FindingSeverity = info|warning|error|critical` → C6 serves
   `blocking|advisory|info`. `palette[severity]` was `undefined`: **no
   badge had a colour**.
4. `ValidationRunStatus` declared `"queued"` → C6 serves `RunStatus.PENDING`.
   The **poll loop never re-armed** for a pending run, and a pending run
   fell through to "the run completed without issues".
5. `RequirementDocumentDetail.content` → C5 serves `body`. **The
   requirement document viewer rendered an empty body.**
6. `ValidationPlugin.plugin_id` / `description` → C6 serves
   `name`/`artifact_formats`/`checks`. The domain selector read
   `"code (undefined v1.0)"` and gave every `<option>` the same
   `undefined` React key.
7. `AllocationCoverageView.is_covered` / `links` → C5 served **neither**.
   `is_covered` was a Python `@property` (computed, never serialised) and
   the links were reduced to id tuples and discarded in `_classify`. The
   workbench threw on `allocation.links.some(…)`.

### Why the test suite could not see any of it

`run-detail.test.tsx:77` mocked `{ findings: [...] }`. **The mock encoded
the UI's wrong belief**, so the test was green while the page crashed —
the same shape as the upload-405 defect, where two sides were each
internally consistent and disagreed with each other.

The fix was not just the types: every affected MSW fixture is now **typed**
with the `lib/types.ts` type instead of `Record<string, unknown>`, which is
what let them drift. The loop is closed: backend → types (coherence gate) →
mock (`tsc`).

### Defect 7 was a contract decision, not a typo

Degrading the UI to what C5 served was **not available**: `R-500-019`
requires auto-accepted requirements to be counted *"from links actually
marked auto-accepted, never inferred from a difference between totals"*,
and no id tuple carries a state. So C5 now serves both — `links`
additively, `is_covered` as a `computed_field` (output-only, so
`R-310-096`'s "a settable conclusion can disagree with its own evidence"
still holds) — at **no extra query**, because `_classify` already had the
links in hand.

**`@property` on a Pydantic model never reaches the wire.** Worth
remembering: it looks like a field in Python and is absent from every JSON
response.

### Then the request direction

The last unguarded half of the same class. 16 bodies compare field-by-field
against a named interface (`JSON.stringify(payload)` where the method
declares `payload: LLMModelUpsert`), 22 by key names, and 24 calls have
their query parameters checked against the route's `required` ones.
Severity there is **symmetric**: the request models are `extra="forbid"`,
so an unexpected key is a 422 and a missing `required` field is a 422 too.

**It found nothing, and that is the finding.** A wrong request key is an
immediate loud 422 in development; a wrong response field is a silent
`undefined`. That asymmetry is why all seven defect groups sat on the
response side. Mutation-proven in both directions all the same (a bogus
field → 2 × `ui_sends_unaccepted_field`; `alias` removed → 2 ×
`ui_omits_required_field`).

### Four defects in the audit itself

Each had made it partially mute, and three produced **confident, wrong**
findings:

| Defect | Effect |
|---|---|
| read only one of the **two** UI wire-type modules | 16 calls wrongly `LOCAL` |
| merged the two namespaces — each declares a different `Finding` | **8 false findings** |
| split interface bodies on `;` without brace depth | **phantom fields** → 8 more false findings |
| a 600-char window ran past the method into the next one | 17 body-less GETs reported as "unclassifiable body" |

Plus a fifth, found when the operator asked to fix the last two advisories:
the rule compared **nullability** against `required`. In JSON Schema
`required` means the KEY is present and says nothing about the value, so
`trigram: str | None` is required AND nullable — the UI's `string | null`
was right, and all 17 reported mismatches were **my false positives**.
Splitting the two rules exposed the check that was missing entirely:
`ui_ignores_nullable`, a value the server may send as `null` that the UI
declares non-nullable. Blocking, mutation-proven, and it reports zero
because the UI is in fact correct.

### Coverage, ratcheted

```
129 UI call sites
  response  COMPARED=117  NO_BODY=11 (204 DELETEs)  OPAQUE=1
  request   type=16  keys=22  none=90  form=1  |  24 name a query param
```

Nothing is silently uncompared. `tests/coherence/test_ui_api_chain.py`
holds four tests, three of which are **vacuity floors** — one per parser
that can break on its own.

---

## Part 2 — The generated OpenAPI document

The operator's goal: *"que la doc fastapi qui est auto generee via le code
source soit la plus exhaustive possible"*, reachable by any authenticated
user.

### Measuring first changed the shape of the task

| Gap | Before | After |
|---|---|---|
| operations with no 4xx | **268 / 272** | 3 (the genuinely public ones) |
| identity headers shown as caller-supplied | **555** | 0 |
| app `description` | 0 / 9 | 9 / 9 |
| `version` | `0.1.0` default | real (`BUILD_VERSION`) |
| `tags` | 10 missing | 0 |
| `/docs` routed at the gateway | **no router at all** | 9 routers |

"Exhaustive" was not primarily a prose problem. The document was
**unreachable** (401 from `AuthGuardMiddleware`, which exempts only
`/health` and `/metrics`, AND no Traefik rule — it fell to `ui-catchall`),
**fragmented** (9 documents for one public surface) and **silent on
refusals**.

### The security finding

`X-User-Id`, `X-Tenant-Id`, `X-User-Roles` and `X-Project-Scopes` were
declared as `Header(...)` parameters and therefore appeared on 272
operations as parameters **the caller supplies**. A reader following the
document was being told to do the one thing `forward-auth-c2` exists to
refuse and `tests/system/test_header_forgery.py` tests. Hidden; the 6
genuinely caller-supplied headers (`If-Match`, `x-turn-id`,
`authorization`, `x-project-id`) are preserved — the filter is exact.

### One post-processor, not 200 edits

FastAPI lets `app.openapi` be replaced by any callable, so
`api_docs.describe_app()` applies every uniform rule once, and a route
added tomorrow inherits them. ~200 `include_in_schema=False` edits and
~268 `responses={...}` edits would each have been forgettable.

### Two places where guessing was refused

**403 is not derivable from a route.** `_require_role(...)` is called in
the handler **body**, not as a `Depends`, so route introspection cannot see
it. Annotating 403 everywhere would have claimed a refusal many routes
cannot produce.

Attaching `ROLE_GATED_RESPONSES` at **router** level was tried and the
audit immediately reported **43 over-claims** — most routers are mixed,
un-gated reads beside gated writes. So the mechanism was inverted:
`describe_app` declares 403 only where `_PROJECT_CONTENT_RE` matches
(`/projects/{id}/…`), which is C2 `_role_gated_project_id`'s own
predicate — the gateway's rule restated, not an inference. The rest
declare it explicitly, and only where EVERY route in the router is gated.
Result: `403_on_ungated_route` = **0**, and blocking.

**And a self-correction worth keeping.** I factored "exempt from the
auth guard" and "needs no token" into one list with a comment claiming
they were the same fact. They are not: `GET /auth/verify` is guard-exempt
*because it verifies a token itself*, so it requires one; and
`POST /auth/login` answers 401 on a wrong password. Two lists now, and
`login`/`token` declare their own 401 with the right meaning.

### The gate's organising distinction

- **BLOCKING** = a statement the document *makes* that is wrong
  (`injected_header_exposed`, `403_on_ungated_route`, `no_summary`,
  `no_tags`). A reader cannot tell a false statement from a true one.
- **RATCHET** = an omission. The reader learns less; nothing they learn is
  wrong. Pinned as a ceiling, may fall, never rise.

### Three defects in this audit too

`--json` was unparseable (components log to stdout on import — the whole
point of the flag); the path comparator ignored the converter suffix
(`{path:path}` vs `{path}`), which made it accuse **7 catch-all routes
wrongly**; and it counted FastAPI's own `HTTPValidationError` /
`ValidationError` fields, making the metric unable to reach zero — which
teaches a reader to ignore it.

---

## Part 3 — Defects found in passing

**The object lock, again, and for a third distinct reason.** A full-suite
gate failed on `test_exactly_one_winner_across_independent_replicas` —
not a flake. The **third** `INSERT` in `locks.py`, the retry after a stale
lock is released, was unguarded: several losers of the first INSERT reach
it together, one wins, and the others got a raw `[ERR 1210]` — a 500 for
behaving correctly, exactly what `_is_acquisition_conflict` prevents on
the other two paths. Fixed with one retry and then a refusal naming the
current holder, never a loop. It appears only under full-suite contention.

**A CI flake that was a test defect.** `retrieval_scan_cap` bounds the
**dense** arm only; in `hybrid` mode the BM25 arm is capped separately and
`service.py` deliberately keeps lexical-only hits *when the dense scan was
truncated*. So the source the test believed was cut came back through
BM25 as soon as the ArangoSearch view had consolidated — passing cold, in
isolation, and failing warm, in a full-suite run. `retrieval_mode="dense"`
is not a weakened precondition but the opposite: with the lexical arm on,
the closing assertion could be satisfied **without KG expansion doing
anything**. Mutation-proven (`kg_repo=None` → fails on the real assertion).

**C2 emitted duplicate, non-deterministic `operationId`s.** FastAPI derives
them from `list(route.methods)[0]` — the first element of a **set** — so a
`methods=["GET","HEAD"]` route produced one id shared by both methods
(invalid OpenAPI) that landed as `…_get` on one build and `…_head` on the
next. Any generated client would churn. Split into two routes, the HEAD
mirror `include_in_schema=False`.

---

## What was NOT done, and why

- **Playwright `tests/system/`** — 5 tests in 2 files, structurally valid
  (they list), **executed by nothing**. `npm run test:system` and
  `playwright.system.config.ts` exist and no workflow calls them. Two real
  blockers: nothing seeds the K8s stack (`source-upload.spec.ts` gets 403),
  and this devcontainer has no browser. Wiring them needs a decision —
  compose (cheaper) or kind (closer to production) — plus a seed step. Left
  to the operator because it is an infra decision, not a mechanical gap.
- **`/docs` in a browser** — `/auth/verify` uses `HTTPBearer` and the
  platform keeps its token in `localStorage`, not a cookie, so a navigation
  carries no header and gets 401. Reachable by any client that sets
  `Authorization: Bearer …`. Browser access would need the UI to proxy the
  spec with the token attached; not built, and said so in `routers.yml`
  and in `api_docs.docs_urls` rather than left to be discovered.
- **The three ratchets** — 1302 of 1393 schema fields with no
  `Field(description=...)`, 101 of 272 operations with no handler
  docstring, 30 role-gated routes without an explicit 403. C6 is the one
  fully-documented component and is the tone reference: describe the
  MEANING and the trap, not the type.

---

## Verification

Both gates green on the same tree, run after every substantive change and
re-run from scratch at the end:

| Gate | Result |
|---|---|
| `ay_platform_core/scripts/run_tests.sh ci` | ruff OK · mypy OK · pytest OK — **All stages OK** |
| `npm --prefix ay_platform_ui run ci` | biome OK · tsc OK · **575 passed** · 4 thresholds |

Four intermediate gate failures, each diagnosed rather than retried: the
object-lock defect (real), the mypy `**dict[str, str]` unpack (135 errors
from one call shape, fixed with a `TypedDict`), a biome import-order break
from the `WorkbenchFinding` rename, and C5's `create_app` crossing ruff's
statement ceiling once a docstring was added (fixed by extracting
`_mount_routers`, the same justification `_wire_traceability` already
carried).

---

## Lesson

Both audits caught defects in themselves — seven between them, four of
which produced confident wrong findings. The property that caught every
one was deliberate: **report the audit's own blind spots as findings**, so
"I could not read this" is never silence. An audit that cannot read one
side of its comparison reports zero drift, and zero drift is
indistinguishable from agreement.

The corollary, which cost the most: **a false statement is worse than a
missing one**, because a reader cannot tell it is false. That is why
`403_on_ungated_route` blocks while `role_gated_without_403` only
ratchets, and why 43 over-claimed 403s were reverted rather than kept for
coverage.
