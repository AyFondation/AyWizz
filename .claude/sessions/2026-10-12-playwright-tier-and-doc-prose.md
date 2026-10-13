<!-- =============================================================================
File: 2026-10-12-playwright-tier-and-doc-prose.md
Version: 1
Path: .claude/sessions/2026-10-12-playwright-tier-and-doc-prose.md
Description: Closing the four items the operator chose from the open list: the
             Playwright system tier (wired, and PASSING 5/5 against a live
             cluster), the documentation ratchets (the 403 dimension closed
             outright; C2/C3/C4/C6/C9/C16 fully documented at the operation
             level), browser-readable `/docs` without introducing a cookie,
             and the Qdrant question answered. The tier's one failing spec
             was the finding: it authenticated as a GLOBAL `admin` and got
             403, which is E-100-002 working as designed rather than a
             defect — a test can encode the opposite of the rule the
             platform exists to keep, and stay plausible while doing it.
============================================================================= -->

# Session — Playwright system tier + documentation prose (2026-10-12)

## Context

Following the resume list written earlier the same day, the operator chose:
**(a)** use K8s and whatever it takes to run the tests properly, **(b)**
fix the doc ratchets, **(c)** browser `/docs` if authenticated, **(d)**
wait, **(e)** answer the Qdrant question.

---

## (a) The Playwright system tier

### It was executed by nothing, and the blockers were concrete

`npm run test:system` and `playwright.system.config.ts` both existed, the
five specs listed correctly, and no script or workflow called them. Two
causes, neither mysterious: `overlays/system-test` sets
`C2_UX_DEV_MODE_ENABLED=false` and `C2_DEMO_SEED_ENABLED=false`
deliberately — so no seeded accounts, so every project-scoped write
answered 403 — and no browser was installed.

### A layered overlay, not three flipped flags

`overlays/ui-system-test` bases on `system-test` with
`behavior: merge` and changes exactly two literals. Flipping them in place
would have published demo credentials from the overlay whose whole value
is being production-shaped — the one the Python `system_k8s` tier uses.
Proven non-lossy by diffing the rendered ConfigMaps: **115 keys on both
sides, nothing dropped, two values changed**. A `behavior: create` there
would have silently replaced the map and dropped ~113 settings.

### Two scripts, one responsibility each

`run_ui_system_tests.sh` runs the tier against whatever the current kube
context points at and **creates and deletes nothing**;
`run_k8s_system_tests.sh --ui` owns the kind cluster's lifetime and
delegates the browser/port-forward logic to it. Two copies of those three
steps would drift.

It port-forwards the **GATEWAY**, not the UI Service, and that is
load-bearing: `runtime-config.json` leaves `apiBaseUrl` empty, so
`apiClient` emits RELATIVE paths and the browser must reach pages and API
through one origin. Forwarding the UI Service would serve every page and
404 every API call — a failure that reads like a broken application
rather than a broken harness.

CI gets job **L5** in `ci-k8s-validate.yml`, and that workflow's path
filter now includes `ay_platform_ui/**`. Its absence would have
reproduced the `.dockerignore` defect exactly: the job that catches the
regression does not run, because the file that caused it is not under
`infra/`.

### Then it ran for real, and found the spec was wrong

Against the live docker-desktop cluster: **4 passed, 1 failed**.
`source-upload.spec.ts` got **403 "requires a project role on this
project"**.

That is not a defect. The spec authenticated as `alice`, a GLOBAL
`admin`, and wrote to project `demo`. **E-100-002 v7 makes `admin` and
`tenant_admin` content-blind, and v8 made the gateway enforce it.** A
test that uses a global administrator to write project content asserts
the opposite of the rule the platform exists to keep — and it looked
entirely reasonable while doing so.

It was also written against a different seeder: `alice`/`seed-password`
and project `demo` come from `seed_e2e.py` (compose), while a K8s stack
is provisioned by C2's own `_ensure_demo_seed`, which grants
`project_editor` to `project-editor` on `project-test`, and grants
`alice` nothing.

Rewired to the demo-seed editor on a demo-seed project, env-overridable.
**5/5 passing**, and **mutation-proven**: a `project_viewer` — who HOLDS
a project role but not write — still fails, so the spec discriminates on
the write gate specifically and not merely on "some identity".

One bug of mine caught on the way: backticks inside a double-quoted
`echo` are command substitution, so a warning message would have tried to
execute `demo-seed-login.spec.ts`. `bash -n` cannot see that.

---

## (c) Browser-readable `/docs`, with no new auth surface

`/docs/<service>` sits behind `forward-auth-c2`, which calls
`/auth/verify`, which reads a bearer **header** (`HTTPBearer`). The
platform keeps its token in `localStorage`, not a cookie, so a browser
NAVIGATION carries no credential and gets 401: reachable by curl,
unreachable by clicking.

Three mechanisms were available and the choice is architectural:

| Mechanism | Cost |
|---|---|
| teach `/auth/verify` a cookie | a **CSRF surface across the whole API**, for a documentation page; this platform has no cookie anywhere, deliberately |
| hand-roll an OpenAPI renderer in the UI | ~200 lines to maintain, no "try it out" |
| **UI page fetches the spec with the bearer, hands it to Swagger UI** | **no new auth surface**, and the CDN is the one `fastapi.openapi.docs` already uses |

The third. A page in the UI's `(protected)` gate — so the existing auth
gate IS the authentication — fetches `/docs/<svc>/openapi.json` with the
bearer and passes Swagger UI the parsed **object** as `spec`, never a
`url`. Swagger therefore issues no request of its own, so there is
nothing for it to fail to authenticate.

The test guards exactly that, and is **mutation-proven**: swapping `spec`
for `url` fails it. Without that assertion the broken version would pass,
because a developer reading the DOM cannot see which key was handed to a
third-party bundle — tabs, header and a spinner that never resolves look
almost right.

"Try it out" is disabled: a submit from this page would carry no bearer,
every call would 401, and the API would read as broken. Asserted, so a
future Swagger upgrade changing the default cannot re-enable it quietly.

---

## (b) The documentation ratchets

### The 403 dimension is closed, and promoted to an absolute

All 30 role-gated routes that lacked a declared 403 now have one, added
**per route**: the six routers involved are mixed (the process surface
has four un-gated reads beside six gated writes; the quota router a
self-service `/quota/me`; the catalog router a public `resolve`), and
FastAPI **merges** router and route `responses` without allowing a
subtraction, so a router-level attachment cannot be excepted. The test
now asserts `== 0` rather than a ceiling: the catalogue calling a route
`ROLE_GATED` is a promise that it refuses.

Three defects of my own along the way: a decorator with no trailing comma
produced `...ConsumptionReport responses=...` (invalid syntax, three
sites); seven single-line decorators crossed the line-length limit; and
the process router's `f"{_TENANT}/..."` idiom with `{{…}}` escapes needed
the base resolved before comparison.

### Prose, by component

**C6** (58 fields) and **C3** (29 fields → 1, plus its 6 operations) are
done at the field level; **C2, C3, C4, C6, C9, C16** are done at the
operation level. 101 → **82** operations without a docstring, 1414 →
**1274** fields. Every number is a ceiling in
`tests/coherence/test_openapi_docs.py` that may fall and never rise.

The tone, fixed by C6 and worth keeping: describe what a field MEANS and
the trap it carries, never its type. `findings_count` says why there is
deliberately no single total; `Verdict.method` says it is what separates
a computed number from a model's opinion; `ImpactPathRange.end_line`
says it is INCLUSIVE, because that is the one thing a caller gets wrong.

Remaining batches: **C5 (57 operations), C8 (14), C7 (11 operations /
218 fields)**.

---

## (e) The Qdrant question — do not split

The bottleneck is **not the engine**. `scan_chunks` issues
`LIMIT @scan_cap` with **no `SORT`** and then computes cosine in pure
Python. So past the cap recall degrades **arbitrarily** rather than
gracefully — you lose arbitrary candidates, not the worst ones, which is
the same mechanism as the BM25/`scan_cap` test flake fixed on
2026-10-09 — and per-query cost is O(chunks scanned) in the application.

Changing engine fixes neither: it would move an unsorted scan to a
filtered HNSW index while **keeping** the Python fusion.

The decisive argument is **locality**. KG expansion depends on it:
`find_neighbor_source_ids` traverses the graph and refetches the
neighbours' chunks in the SAME database. Splitting turns one traversal
into two network round-trips with an application-side join, on the hot
path.

Correct order: **measure** (`R-310-300…305`, the NFR envelope —
increment 9, unbuilt, which is why "it will not scale" is an intuition
and not a measurement), then ArangoDB 3.12.4.3's native vector index
(`APPROX_NEAR_COSINE`, behind `--experimental-vector-index`), then
reconsider.

If a split happens anyway: **Qdrant** — Rust, one container, no external
dependencies (unlike Milvus, which brings etcd + MinIO + Pulsar),
first-class FILTERED HNSW matching the existing
tenant/project/index/model_id filters, and snapshots that fit C16's
model. The one alternative worth naming is **pgvector**, and only if
Postgres arrives for other reasons — otherwise it is a second storage
engine for a single use.

---

## Verification

| Gate | Result |
|---|---|
| `run_tests.sh ci` | ruff OK · mypy OK · pytest OK |
| `npm run ci` | biome OK · tsc OK · **580 passed** · 4 thresholds |
| `run_ui_system_tests.sh` | **5/5 against the live docker-desktop cluster** |

One UI flake characterised rather than ignored:
`source-detail.test.tsx::shows an empty state when the source has no runs`
failed once in a full run and passes in isolation and on the next full
run — an async `waitFor` on an empty state, load-dependent. It is one of
the two unidentified failures recorded on 2026-10-06, now with a
signature: a TIMING flake, not a regression (the file and the sources
pages are untouched by any recent diff).

One mypy failure worth recording for its shape: `**docs_urls(...)`
returning `dict[str, str]` produced **135 errors from one call site** —
mypy tries the unpack against every `FastAPI` parameter in turn. A
`TypedDict` names the keys and makes the unpack checkable.

---

## Lesson

The tier that had never run is the one that found a test asserting the
opposite of a security rule. Four tiers were green on that spec's
contract — the mocked UI tier, the contract tier, the backend tiers —
because none of them authenticates as a real identity against a real
gate. **A test's identity is part of its contract**, and a test that
picks an identity the platform is designed to refuse will fail for the
right reason only once something actually runs it.
