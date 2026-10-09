# =============================================================================
# File: test_ui_api_chain.py
# Version: 4
# Path: ay_platform_core/tests/coherence/test_ui_api_chain.py
# Description: Refuses a response FIELD the UI reads and the backend does not
#              serve — the sixth and last link of the UI→API chain, and the
#              only one that had no guard.
#
#              WHAT THE OTHER FIVE ALREADY COVER. `api-surface.test.ts`
#              pins every path the real client emits to the route catalogue;
#              `test_route_catalog.py` pins the catalogue to the live FastAPI
#              routers; `test_ui_contract_snapshot.py` pins the JSON snapshot
#              between them; `test_gateway_route_coverage.py` pins every
#              catalogued route to its winning Traefik router and to
#              `forward-auth-c2`; `audit_functional_coverage.py` demands a
#              test per endpoint. Composed, those prove a UI call REACHES
#              the right handler behind the authorization boundary.
#
#              None of them looks at what comes BACK. Nothing in the
#              repository read an OpenAPI schema before this file, so a
#              renamed response field was invisible to every tier: the
#              backend's tests assert the new name, and the UI's assert the
#              old one against an MSW mock that encodes the UI's belief
#              instead of the server's behaviour. `run-detail.test.tsx`
#              mocked `{ findings: [...] }` while C6 serves
#              `FindingPage.items`, so the validation run page threw
#              `undefined.length` in production with its test green — the
#              same two-internally-consistent-sides shape as the upload-405
#              defect.
#
#              v4 (2026-10-09) splits `required` from NULLABLE. They were
#              one rule, and that rule was wrong in both directions: it
#              reported seventeen false positives (a required-but-nullable
#              field whose UI type was already correct) while missing the
#              dangerous case altogether — a value the server may send as
#              `null` that the UI declares non-nullable, which no compiler
#              will guard. `ui_ignores_nullable` now blocks on it.
#
#              v3 (2026-10-07) adds THE REQUEST DIRECTION, which was the
#              last unguarded half of the same defect class: paths and
#              methods were pinned, response shapes were pinned, and what
#              the client SENDS was compared against nothing. 16 bodies
#              now compare field-by-field against a named interface, 22
#              by key names, and 24 calls have their query parameters
#              checked against the route's `required` ones.
#
#              It found NOTHING, and that is itself a finding: a wrong
#              request key is an immediate, loud 422 in development,
#              while a wrong response field is a silent `undefined`. That
#              asymmetry is why all seven defect groups this audit has
#              found sat on the response side. The guard still earns its
#              place — the next 422 will be caught before a human sees
#              it — but the pressure was never equal.
#
#              v2 (2026-10-07) widened the audit and the widening paid for
#              itself three times: reading `lib/workbenchTypes.ts` (the
#              platform has TWO wire-type modules, not one) and unwrapping
#              root-level list responses took the compared count from 98 to
#              115, and surfaced `AllocationCoverageView` — which had
#              declared `is_covered` and `links` since it was written while
#              C5 served NEITHER, so the workbench threw on
#              `allocation.links.some(…)`. C5 now serves both, because
#              `R-500-019` requires auto-accepted requirements to be
#              counted from real link state and no id tuple carries one.
#
#              FIRST RUN FOUND FOUR, all of them live:
#                · `FindingPage.findings/limit/offset` → C6 serves
#                  `items`/`run_id`/`total`. Hard crash on the run page.
#                · `ValidationRun.total_findings` → C6 serves
#                  `findings_count: {blocking, advisory, info}`. Rendered
#                  the string "undefined".
#                · `RequirementDocumentDetail.content` → C5 serves `body`.
#                  Document viewer rendered an empty body.
#                · `RequirementEntity.payload` → C5 serves `title`/`body`
#                  and the relation fields; there is no `payload`.
#
#              WHY NO WAIVER LIST. The audit distinguishes a REQUIRED TS
#              field from an optional one: `x?: T` absent from the schema is
#              reported but not blocking, because the declaration already
#              says "may be absent". That removes the whole class of finding
#              a waiver would otherwise be needed for, so the blocking count
#              is a ratchet at zero and an entry here would be a promise the
#              code does not keep.
#
#              NO `@relation validates:` MARKER, deliberately — same reason
#              as `test_gateway_route_coverage.py`: this guards a DEFECT
#              CLASS, not a requirement.
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "checks"
sys.path.insert(0, str(_SCRIPTS))

from audit_ui_api_chain import Report, audit  # noqa: E402

pytestmark = pytest.mark.coherence


@pytest.fixture(scope="module")
def report() -> Report:
    """One audit per module — it imports nine component apps."""
    return audit()


def test_no_ui_field_is_absent_from_the_served_schema(report: Report) -> None:
    """A required TS field the backend never sends is a runtime defect.

    Not a style issue: `page.findings` was `undefined` and
    `findings.length` threw, taking the whole validation run page with it.
    `String(run.total_findings)` rendered the literal "undefined" into the
    summary. Both tiers were green throughout.
    """
    blocking = report.blocking
    assert not blocking, (
        f"{len(blocking)} response field(s) the UI reads are not served by "
        "the backend. Fix the SIDE THAT IS WRONG, not this test: the "
        "backend models are the wire contract (spec-traced, `extra=forbid`) "
        "and `ay_platform_ui/lib/types.ts` states it mirrors them verbatim, "
        "so the UI type is normally the one to correct — along with every "
        "page reading the old name AND the MSW mock that encodes it, or the "
        "UI tier stays green on the stale shape.\n\n  "
        + "\n  ".join(f"{f.http} {f.path}\n      {f.detail}" for f in blocking)
    )


def test_the_audit_is_still_reading_both_sides(report: Report) -> None:
    """Guards the audit against going vacuous.

    Every assertion above is an absence, so a parser that stops matching —
    a renamed `this.request` helper, a component whose `main` module fails
    to import, a `types.ts` reformatted past the field regex — makes this
    file pass while comparing nothing. Three independent floors:

      · the UI call sites still parse (the client has ~130);
      · a majority of them reach the COMPARED state, i.e. a resolved
        backend schema AND a known TS interface on the other side;
      · no call site and no component was skipped for a reason the audit
        itself classifies as its own blindness.
    """
    assert len(report.rows) > 100, (
        f"only {len(report.rows)} UI call sites parsed out of ~130 — the "
        "`this.request<T>(path, {method})` extractor no longer matches the "
        "client's call style and this file is now vacuous"
    )
    compared = [r for r in report.rows if r.schema_state == "COMPARED"]
    assert len(compared) >= 115, (
        f"only {len(compared)} of {len(report.rows)} call sites were "
        "compared field-by-field; the rest resolved to UNDECLARED / OPAQUE "
        "/ LOCAL / NO_BODY / UNMATCHED, so the audit is running but looking "
        "at less than it did. 117 of 129 were compared on 2026-10-07 — the "
        "other 12 are eleven 204 DELETEs and one opaque body, so NOTHING is "
        "silently uncompared any more. That is a RATCHET, and it got there "
        "in four steps: unwrapping root-level list responses (98 → 101), "
        "reading `lib/workbenchTypes.ts` alongside `lib/types.ts` "
        "(101 → 115), promoting five inline object types to named "
        "interfaces, and naming the last two response types that were "
        "declared inside `apiClient.ts` (115 → 117). A drop means a parser "
        "stopped matching, not that the UI shrank."
    )
    blind = [
        f
        for f in report.findings
        if f.code in ("unparsed_call_site", "component_unreadable")
    ]
    assert not blind, (
        "the audit could not read part of its own input:\n  "
        + "\n  ".join(f.detail for f in blind)
    )


def test_the_request_direction_is_still_being_compared(report: Report) -> None:
    """The REQUEST side has its own vacuity floor, deliberately separate.

    The two directions go vacuous independently, and that is not
    hypothetical: the response comparison worked for a whole session
    while the request side simply did not exist, and nothing said so. A
    single combined floor would have been satisfied by the response half
    alone.

    Three independent signals, each pinned to a parser that can break on
    its own:
      · bodies typed by a NAMED interface, compared field by field —
        `JSON.stringify(payload)` where the method declares
        `payload: LLMModelUpsert`;
      · bodies compared by KEY NAMES — inline literals, which still
        catch the 422 class;
      · calls naming a query parameter, read as literal `?x=` /
        `params.set("x")` text out of the method body because the client
        assembles the string into a variable first.
    """
    states = report.request_states
    assert states.get("type", 0) >= 14, (
        f"only {states.get('type', 0)} request bodies resolved to a named "
        "interface; 16 did on 2026-10-07. Either `JSON.stringify(<ident>)` "
        "stopped being the idiom or `_declared_param_type` no longer reads "
        "the method signature"
    )
    assert states.get("keys", 0) >= 20, (
        f"only {states.get('keys', 0)} request bodies yielded key names; 22 "
        "did on 2026-10-07 — `_object_literal_keys` has stopped matching"
    )
    assert report.query_named >= 20, (
        f"only {report.query_named} calls yielded a query-parameter name; 24 "
        "did on 2026-10-07. Every `ui_omits_required_query_param` check "
        "depends on this scan, so a drop silences them all without failing "
        "anything else"
    )


def test_every_path_resolves_to_a_backend_operation(report: Report) -> None:
    """`path_unmatched` is the audit's own blind spot, not a contract break.

    Existence is owned by `api-surface.test.ts`, which drives the REAL
    client rather than parsing it — so an unmatched row here means this
    script failed to resolve a path, and that row is silently unaudited for
    shape. Twelve were, on the first run: the client interpolates query
    strings at the END of a template (`` `/admin/users${qs}` ``), which the
    extractor could not tell from a final path parameter. Kept at zero so
    the next such case surfaces as a failure instead of as twelve rows
    quietly dropping out of the comparison.
    """
    unmatched = [f for f in report.findings if f.code == "path_unmatched"]
    assert not unmatched, (
        f"{len(unmatched)} UI call(s) resolved to no OpenAPI operation, so "
        "their response shape is UNAUDITED. This is a gap in "
        "`scripts/checks/audit_ui_api_chain.py` (path normalisation), not "
        "necessarily a missing route — check `api-surface.test.ts` for the "
        "existence verdict.\n\n  "
        + "\n  ".join(f"{f.http} {f.path}" for f in unmatched)
    )
