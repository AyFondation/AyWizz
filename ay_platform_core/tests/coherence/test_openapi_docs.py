# =============================================================================
# File: test_openapi_docs.py
# Version: 2
# Path: ay_platform_core/tests/coherence/test_openapi_docs.py
# Description: Guards the OpenAPI document each component generates, so the
#              documentation cannot quietly rot back to what it was.
#
#              WHAT IT WAS, measured on 2026-10-09 before any of this: 268
#              of 272 operations declared no 4xx, so the document could not
#              tell a consumer that an endpoint refuses them; 555
#              parameters advertised the gateway-injected identity headers
#              as caller-supplied, which is precisely what
#              `forward-auth-c2` exists to refuse and what
#              `tests/system/test_header_forgery.py` tests; every app
#              carried the default `version="0.1.0"` with no description;
#              and no router claimed `/docs` at the gateway, so none of it
#              was reachable anyway. None of that failed a test, because
#              nothing in the repository read the document.
#
#              TWO KINDS OF ASSERTION, deliberately separated.
#
#              The BLOCKING ones are statements the document MAKES that
#              would be wrong: an injected header offered as a parameter,
#              an operation with no summary or no tag, and a 403 on a route
#              that cannot produce one. A reader cannot tell a false
#              statement from a true one, so none may ship.
#
#              The RATCHETS are omissions — operations with no description,
#              role-gated routes whose 403 is not yet declared, fields with
#              no prose. A reader learns less than they could and nothing
#              they learn is wrong. Those are being written in batches, so
#              the gate pins today's count and refuses regression, the same
#              shape as `test_project_role_gate_ratchet.py`. A number that
#              can only improve beats a target nobody meets.
#
#              NO `@relation validates:` MARKER, deliberately — this guards
#              a defect class, not one requirement.
# =============================================================================

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "checks"
sys.path.insert(0, str(_SCRIPTS))

from audit_openapi_docs import Report, audit  # noqa: E402

pytestmark = pytest.mark.coherence


@pytest.fixture(scope="module")
def report() -> Report:
    """One audit per module — it imports nine component apps."""
    return audit()


def test_the_document_makes_no_false_statement(report: Report) -> None:
    """Nothing the document asserts may be untrue.

    Three of the four codes here were real: the injected headers were
    advertised on 272 operations, and a router-level 403 claimed a refusal
    43 operations could not produce before the rule was narrowed to the
    paths the gateway actually gates.
    """
    blocking = report.blocking
    assert not blocking, (
        f"{len(blocking)} operation(s) carry a statement that is wrong or "
        "missing outright:\n  "
        + "\n  ".join(
            f"[{f.code}] {f.component} {f.http} {f.path}\n      {f.detail}"
            for f in blocking
        )
    )


def test_every_component_describes_itself(report: Report) -> None:
    """An app with no description opens `/docs` on a bare path list.

    All nine were like that: `FastAPI(title="C5 Requirements Service")` and
    nothing else, so a reader arriving at the documentation learned the
    component's name and had to infer the rest from endpoint paths.
    """
    assert not report.apps_without_description, (
        "these components generate a document with no description: "
        + ", ".join(report.apps_without_description)
    )


def test_the_audit_can_read_every_component(report: Report) -> None:
    """Guards the audit against going vacuous.

    Every assertion here is an absence, so a component that fails to
    import makes them all pass while checking nothing of it. This is not
    hypothetical for this codebase: `pyproject.toml` sets
    `filterwarnings = error`, and a schema warning raised as an exception
    once dropped C2 out of a sibling audit entirely while every assertion
    stayed green.
    """
    assert not report.import_errors, (
        "the audit could not read part of its own input:\n  "
        + "\n  ".join(report.import_errors)
    )
    assert report.operations > 250, (
        f"only {report.operations} operations were read; 272 existed on "
        "2026-10-09, so the component list or the schema walk has stopped "
        "matching"
    )
    assert report.fields > 1250, (
        f"only {report.fields} schema fields were read; 1393 existed on "
        "2026-10-09 — the `components.schemas` walk is no longer finding "
        "them, and every field-level count below is then meaningless"
    )


def test_operation_prose_does_not_regress(report: Report) -> None:
    """RATCHET. 101 of 272 operations have no description.

    FastAPI derives an operation's `description` from the handler's
    docstring, so this number is exactly "how many handlers are
    undocumented". It may fall and SHALL NOT rise: a new route arriving
    without a docstring would otherwise be invisible.
    """
    missing = report.count("no_description")
    assert missing <= 101, (
        f"{missing} operations have no description, up from 101 on "
        "2026-10-09. FastAPI reads this from the handler's docstring — add "
        "one to the new route rather than raising this number."
    )


def test_role_gated_403_coverage_does_not_regress(report: Report) -> None:
    """RATCHET. 30 role-gated routes do not yet declare their 403.

    `describe_app` declares it automatically wherever the GATEWAY's own
    predicate applies — a project-content path — because that is derivable
    and therefore cannot be wrong. The remainder are gated on something
    else (a platform role on a tenant, a session, a user), which
    `_require_role(...)` decides in the HANDLER BODY where no introspection
    can see it. Those declare `responses=ROLE_GATED_RESPONSES` explicitly,
    and the number of ones that have not yet is what this pins.

    Attaching it to a whole router is how it goes wrong: most routers are
    mixed, and a router-level 403 produced 43 false claims. The blocking
    `403_on_ungated_route` test above is the referee for that.
    """
    missing = report.count("role_gated_without_403")
    assert missing <= 30, (
        f"{missing} role-gated routes declare no 403, up from 30 on "
        "2026-10-09. Add `responses=ROLE_GATED_RESPONSES` to the route "
        "(not the router, unless EVERY route in it is gated)."
    )


def test_field_prose_does_not_regress(report: Report) -> None:
    """RATCHET. 1302 of 1393 schema fields carry no description.

    The largest remaining gap and the only one that is pure prose: these
    are the request and response bodies, and no rule derives what a field
    MEANS. Pinned as a ceiling so the batches that reduce it cannot be
    undone by a new model landing bare.

    1414 of 1447 before the first batch. The totals moved too, and for a
    reason worth recording: FastAPI's own `HTTPValidationError` and
    `ValidationError` are excluded now (54 fields across nine
    components), because their prose is not ours to write and a metric
    that cannot reach zero teaches the reader to ignore it. C6's models
    account for the other 58.
    """
    assert report.undescribed_fields <= 1302, (
        f"{report.undescribed_fields} schema fields have no description, up "
        "from 1302 on 2026-10-09. Add `Field(description=...)` to the new "
        "model's fields."
    )
