<!-- =============================================================================
File: 2026-10-06-authorization-boundary-test-audit-and-agpl.md
Version: 1
Path: .claude/sessions/2026-10-06-authorization-boundary-test-audit-and-agpl.md
Description: An advanced test audit that found the suite was reporting health it
             had not measured, and whose fixes uncovered a live authorization
             hole: 70 endpoints across C4, C5 and C7 promised a project scope
             with nothing enforcing it. Closed in two layers sharing one
             predicate — a gateway boundary at /auth/verify and an in-app
             router floor — then verified on the running compose stack AND on
             the live K8s deployment, where the exposure was confirmed present
             before the rebuild. Also: an 83-endpoint routing hole that made
             most of the 310-SPEC capability unreachable, a lock whose
             concurrency defect was NOT what I first claimed, three gate
             corrections, the relicensing to AGPL-3.0-or-later with its §13
             source offer, and a CI race that had been reddening the upload
             tests. Recurring lesson, paid for three times in one session: a
             test that fabricates the header, serialises the call or invents
             the identifier proves nothing it claims to prove.
============================================================================= -->

# Session — Authorization boundary, test audit, AGPL (2026-10-06)

## Context

The session began on a request to finish three known system-test failures and
drifted — correctly — into an audit of whether the test suite measures what it
reports. It does not, in several specific and consequential ways. Everything
below was found by reading and then VERIFIED against a running stack; the
claims that did not survive verification are recorded as such, because three
of my own diagnoses were wrong and the record is worth more than the
appearance of competence.

## Bloc 1 — An 83-endpoint routing hole

Cross-referencing the 262-endpoint route catalogue against Traefik's rules
showed that only `/requirements` of C5's project surface was routed. Ten other
prefixes (`containers`, `coverage`, `intake`, `plans`, `process`, `changes`,
`baselines`, `absorb`, `baseline-readiness`, `impact`) matched the generic
`c2-projects` rule and were delivered to **C2**, which does not implement
them; `/api/v1/process` had no rule at all and fell to the UI catch-all. Plus
six C4 source-browser endpoints.

Confirmed live before the fix: `/requirements/documents` returned 200 with real
data while every one of those prefixes returned 404 or 502. **Essentially the
whole 310-SPEC traceability capability, built across increments 3–8, was
unreachable in deployment** — and every backend tier stayed green throughout,
because all of them mount the routers in-process.

Fixed with `c5-project-surfaces`, `c5-process-tenant`, `c4-source` and
`c6-validation-project`. `tests/coherence/test_gateway_route_coverage.py` now
fails the build on any catalogued route that is unrouted, misrouted, or
missing its auth middleware — mutation-proven on three real mutations (the
first mutation I tried renamed a router KEY and changed nothing, which is how
I learned the matcher ignores names).

## Bloc 2 — The authorization hole, and why the matrix could not see it

Widening `test_isolation.py`'s `_is_item_endpoint` (it matched only a TRAILING
`{..._id}`/`{slug}`/`{version}`, so any sub-resource suffix reclassified an
item endpoint as a "list") brought **62 misclassified endpoints** back into the
cross-project isolation test. Among them ten GETs streaming another project's
bytes, including `/backups/{backup_id}/download` and
`/sources/{source_id}/blob`.

That exposed two things the matrix structurally cannot test:

1. **It fabricates the header whose derivation IS the enforcement.**
   `build_forward_auth_headers` emits the profile's roles with no reference to
   the request path, so a foreign-project caller was handed their own
   `project_owner` on somebody else's path.
2. **It accepts a 404 on an invented resource id as proof of isolation.** An
   un-gated endpoint produces that for free.

Pulling the thread: **70 endpoints catalogued `Auth.AUTHENTICATED` +
`Scope.PROJECT`** — a scope promised, nothing enforcing it. C5's whole read
surface, C7's 14 source reads, C4's documents surface including `POST`, `PUT`,
`DELETE`, `mkdir`, `rename`, `move`. Verified live: a caller holding
`project_owner` on `demo` alone received **200** from
`/api/v1/projects/not-mine/requirements/entities`; the body was empty only
because that project had no data, and the authorization decision was ALLOW.
C5 compounds it by keying entities `project_id:entity_id` with no tenant
component, so the exposure crosses tenants there.

Re-probed on the **live K8s deployment**: same 200. The exposure was in
production, not hypothetical.

## Bloc 3 — Closed in two layers sharing one predicate

**The gateway** (`_role_gated_project_id`, E-100-002 v8): `/auth/verify`
refuses a project-content request from a caller with no grant on that project.
One check, at the only layer that knows both the target project (from the URI)
and the caller's full scope map (from the verified JWT) — so it covers routes
nobody has written yet, and it cannot break the service-to-service callers
because none of them traverse C1. Exempt, reusing the existing
content/governance split: `/admin/`, the bare project resource, `/members`,
and `/backups` (D-022's operator surface — verified against the catalogue to
be the ONLY project-scoped rows accepting a global role).

**In-app** (`require_project_content_role`): attached to the twelve
project-content routers, so a component reached directly meets a gate too, and
a new route inherits one. It self-limits to `/projects/<id>/` paths —
necessary, because `c7_memory`'s router also serves `/api/v1/memory/retrieve`,
which C3 calls with global roles only; a blanket gate would have returned 403
to every RAG query in the platform.

Plus an editor-level gate on C4's six document mutations, which the viewer
floor alone would have left open to a `project_viewer`.

**Three properties, each wrong once in my hands:** the self-limiting (caught
by reasoning before shipping), 401-before-403 (caught by nine
`test_anonymous_*` tests — a router dependency runs ahead of the route's own
`_require_actor`, so anonymous callers got 403), and the stripping of
content-blind global roles.

C9's MCP adapter sent NO headers at all for C5 reads and would have 401'd the
moment the floor landed; it now reads C9's own request context and forwards the
caller's PROVEN role for the project the tool argument names. Nothing is
invented — a caller with no grant sends an empty role list and is refused.

Catalogue: 0 project-scoped endpoints un-gated, 136 ROLE_GATED. The ratchet is
emptied and inverted: it now asserts the list STAYS empty.

## Bloc 4 — Three of my own diagnoses were wrong

**The lock.** I claimed ArangoDB's `UPSERT` non-atomicity produced "2 holders
granted the same lease". Wrong: `LockManager._run` wraps every call in an
`asyncio.Lock`, so calls on one manager never overlap. My first stress test —
320 acquisitions — was therefore **vacuous**, and mutation proved it: the old
racy implementation passed it identically. Rewritten with **eight independent
LockManagers** over one database (production runs N replicas; only the DATABASE
serialises them), it immediately found a bug in my own fix (Arango raises
`ERR 1200 write-write conflict`, not only 1210) and, by mutation, fails on
round 0 against the old UPSERT. The real defect: under genuine concurrency the
loser of the race received a database error instead of `LockHeldError` — a 500
for behaving correctly, on the path whose job is naming the holder (R-310-193).
**The original symptom remains unexplained**; I did not establish that this fix
causes it.

**The graph.** I reported "there is no graph RAG in the generation path". False
— there is a real `ANY 1..depth` AQL traversal with pool widening and a 1.3
ranking boost, wired in C7's composition root, plus BM25/RRF fusion. I had
concluded absence from a `grep | head -12` that truncated exactly the matching
file.

**The activation check.** Three versions, the first two written confidently and
both vacuous: an HTTP probe through the gateway (answered 401 by forward-auth
before reaching n8n, so green on a stack with no workflow) and an unbounded
`docker logs` grep (matched the previous boot instantly).

## Bloc 5 — Test-gate corrections

- `coverage.exclude_also` lost `"pass"` and `"\.\.\."`. The first excluded
  every `except …: pass` body — the platform has nine
  `contextlib.suppress(Exception)` sites, so the riskiest construct in the
  codebase was exempt from the measurement the gate exists for. Cost: 15
  statements, immaterial at ~13k.
- `PytestUnraisableExceptionWarning` downgraded from error to a printed
  warning, after tracing the leaked event loop with `-X tracemalloc` to
  **pytest-asyncio's own loop management**, not platform code. As an error it
  failed whichever test the GC interrupted — a different one each run, which
  two consecutive runs demonstrated.
- Quality FLOORS on semantic retrieval, derived from measurement (recall@3,
  MRR, nDCG@5, near-dup and multi-hop recall@3), where the file previously
  said "don't assert a floor". The graph arm is still NOT evaluated — it needs
  a KG over the golden corpus, and that is the precondition for measuring any
  graph investment.
- `ci-system-tests` regained its `pull_request` trigger; its own documented
  condition ("restore it once a run has gone green") was met.
- `.dockerignore` and `infra/c1_gateway/**` added to the K8s path filter. The
  former is the file whose `**/coverage` pattern made C5 un-deployable for five
  increments, and it was not in the filter.
- Two `suppress(Exception)` sites now log with a traceback: auto-KG extraction
  and conversation-turn ingestion. Both silently degrade answer quality — the
  second drops the CONVERSATIONS index, so the assistant stops remembering
  prior turns while everything stays green.

## Bloc 6 — Relicensing to AGPL-3.0-or-later

The operator asked for GPLv2 "so anyone deploying it must contribute". GPLv2
does not do that: its copyleft triggers on DISTRIBUTION, so a hosted service
distributes nothing and owes nothing — precisely the case the change was meant
to cover. It is also incompatible with two main dependencies (`minio` and
`kubernetes_asyncio` are Apache-2.0, whose patent clause the FSF treats as an
additional restriction). AGPLv3 §13 is the clause that reaches a network
deployment. Operator chose AGPLv3.

Noted in passing: both tier Dockerfiles ALREADY carried
`org.opencontainers.image.licenses="AGPL-3.0-or-later"` while `pyproject.toml`
said `Proprietary` — the images had been advertising AGPL for some time.

Delivered: verbatim FSF text at the root (copied from a local source, validated
on five canonical markers including §13 — a legal document is not written from
memory); SPDX identifiers in `pyproject.toml` (PEP 639 string form — the table
form is rejected outright by this setuptools) and `package.json`;
`COPY LICENSE` in both images, which shipped none; and the **§13 source offer**
in the UI root layout, outside the providers, because `BuildStamp` renders
nothing until the config bootstrap is ready and `/login` is exactly where an
unauthenticated network user arrives.

Deliberately NOT done, deferred by the operator: per-file licence headers, and
a CLA or dual-licensing. Worth restating: the AGPL forces source AVAILABILITY
to recipients, not contribution back upstream.

## Defects fixed in passing

- `/requirements/entities/{entity_id}/history` had no role gate at all —
  caught because it returns `200 {"history": []}` for a non-existent resource
  where its siblings 404. Gated at viewer level.
- Two tests asserted a global `admin` can write project content anywhere. That
  has been impossible since E-100-002 v7 (every `_require_role` strips
  `admin`); they passed only because their `UPLOAD_ROLES` constant still
  contained `"admin"` and compared against the EMITTED header rather than what
  a gate does with it. Both inverted.
- The lifecycle-enforcement fixture never granted its user a project role, so
  four tests could not reach their assertion once the boundary landed.
- `test_isolation`'s `_interpolate` carried a hardcoded list of eleven
  placeholder names; a path containing any other (`{container}`, `{tag}`,
  `{fmt}`, `{path:path}`) kept its literal braces, matched no route, and
  returned 404 — satisfying "no leak" on an endpoint never reached.
- A false cross-reference: `test_isolation`'s header claimed list-endpoint
  leakage was "covered by `test_backend_state.py`". It is not — that file holds
  eight write-persistence tests and no cross-project assertion. An exclusion
  justified by coverage that does not exist is worse than an unjustified one.
- `activate-workflows` raced n8n's webhook registration, which is what had been
  failing the upload tests in CI.

## Open questions

- **69 endpoints still have no in-app gate** → now zero: closed in this
  session. The ratchet keeps it so.
- **Playwright system specs run in no CI.** Prerequisite demonstrated, not
  assumed: `run_k8s_system_tests.sh` already builds and deploys the UI image,
  but nothing seeds the K8s stack, so `source-upload.spec.ts`'s contract
  returns 403 there. Needs a seed step. Not wired here because this
  environment has neither `kind` nor a Playwright browser, and shipping
  unverified CI wiring is the defect this session spent its time removing.
- **The graph arm in the retrieval eval** needs a KG over the golden corpus
  (an LLM, i.e. a fixture).
- **The pytest-asyncio loop leak** is classified, not fixed; the mixed
  `loop_scope` values remain to unify.
- **Two UI test failures** on a single run, never reproduced across four clean
  runs, never identified.
- **`pytest-randomly`** is installed and verified in both directions, but
  nothing runs it — a scheduled non-blocking job is the intended home.

## A lesson about method

Three times in one session a test of mine looked correct and measured nothing:
it fabricated the header that constitutes the enforcement, serialised the calls
it claimed to race, or matched a log line from before the event. Each was
caught by deliberately trying to make the test fail — mutation, a `--since now`
window, reading what the fixture actually sends. The suite's own blind spots had
the same shape at a larger scale: the auth matrix injects the headers whose
derivation is the security property, and reads a 404 on an invented id as proof
of isolation. **A green test is evidence only to the extent that something was
shown to turn it red.**

## Verification

- Backend `run_tests.sh ci`: All stages OK across the session, final run with
  the full gating change in place.
- System tier against the real compose stack: **50 passed, 1 skipped, 0
  failed**, including every MCP tool flow — the check that proved the
  service-to-service callers survived the new floor.
- UI `npm run ci`: **575 passed**, four coverage thresholds held.
- Live K8s: the exposure confirmed present before the rebuild, then
  `stop.sh dev` (no `--wipe`, data preserved) → both images rebuilt →
  `run.sh dev --wait`. After: foreign-project reads 403, the previously
  unroutable prefixes 200 with real payloads through `project-editor`, the
  AGPL §13 offer served unauthenticated on `/login`.
- Behavioural consequence to expect: `tenant-admin`, `superroot` and `alice`
  hold no project scope in the seeded K8s stack, so opening a project as any
  of them now returns 403. That is E-100-002 v7's content-blindness finally
  being enforced rather than contradicted by 70 endpoints.
