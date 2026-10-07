# =============================================================================
# File: test_project_role_gate_ratchet.py
# Version: 3
# Path: ay_platform_core/tests/coherence/test_project_role_gate_ratchet.py
# Description: Refuses a project-scoped endpoint that promises a project
#              scope without enforcing one. The waiver list is now EMPTY,
#              and that is the point of keeping the file.
#
#              WHAT THE AUDIT FOUND (2026-10-06). 70 endpoints across C4, C5
#              and C7 were catalogued `Auth.AUTHENTICATED` + `Scope.PROJECT`
#              — a contradiction: `Scope.PROJECT`'s contract is that a
#              cross-project call returns 403/404, and `AUTHENTICATED` means
#              no role is checked, so nothing enforced it. Verified against
#              the running stack: a caller holding `project_owner` on `demo`
#              and nothing else received **200** from
#              `/api/v1/projects/not-mine/requirements/entities`. The body
#              was empty only because that project had no data; the
#              authorization decision was ALLOW. Re-verified on the live K8s
#              deployment, where the same call returned 200 as well.
#
#              HOW IT WAS CLOSED, in two layers that share one predicate:
#                1. the GATEWAY — `/auth/verify` refuses a project-content
#                   request from a caller with no grant on that project
#                   (`c2_auth.router._role_gated_project_id`). One check
#                   covering every current and future route, and it cannot
#                   break the service-to-service callers because none of
#                   them traverse C1;
#                2. IN-APP — `require_project_content_role` attached to the
#                   twelve project-content routers
#                   (`c2_auth.forward_auth`), so a component reached
#                   directly still meets a gate, and a route nobody has
#                   written yet inherits one.
#              Plus an editor-level gate on C4's six document MUTATIONS,
#              which the viewer floor alone would have left open to a
#              `project_viewer`.
#
#              WHY THE FILE SURVIVES AN EMPTY WAIVER. The list is the thing
#              that made 70 endpoints countable instead of invisible. Kept
#              at zero, it now asserts the stronger property: a NEW
#              project-scoped endpoint SHALL declare a role gate. The
#              isolation matrix will not object on its own — it accepts a
#              404 on a fabricated resource id as proof of isolation, which
#              an un-gated endpoint produces for free.
#
#              NO `@relation validates:` MARKER, deliberately — this guards
#              a defect class, not one requirement.
# =============================================================================

from __future__ import annotations

import pytest

from tests.e2e.auth_matrix._catalog import ENDPOINTS, Auth, Scope

pytestmark = pytest.mark.coherence


#: Project-scoped endpoints with no declared role gate. EMPTY since
#: 2026-10-06, and it SHALL stay empty: an entry here is a promise the code
#: does not keep. If a route genuinely cannot carry a gate, that is a
#: conversation, not a line in this set.
_KNOWN_UNGATED: frozenset[tuple[str, str]] = frozenset()


def _ungated_project_endpoints() -> set[tuple[str, str]]:
    """Endpoints claiming a project scope with no role gate."""
    return {
        (e.method, e.path)
        for e in ENDPOINTS
        if e.auth == Auth.AUTHENTICATED and e.scope == Scope.PROJECT
    }


def test_no_project_scoped_endpoint_lacks_a_role_gate() -> None:
    """`Scope.PROJECT` + `Auth.AUTHENTICATED` is a promise nothing keeps.

    The scope says a cross-project call is refused; `AUTHENTICATED` says no
    role is checked. Seventy endpoints sat in that contradiction until
    2026-10-06 and the suite reported them as fine, because the isolation
    matrix reads a 404 on a made-up resource id as proof of isolation.
    """
    offenders = _ungated_project_endpoints() - _KNOWN_UNGATED
    assert not offenders, (
        f"{len(offenders)} project-scoped endpoint(s) declare "
        "`Auth.AUTHENTICATED`, i.e. a project scope with no role gate. Any "
        "authenticated caller can reach the project they name.\n\n"
        "Fix the ROUTE, not this list: the twelve project-content routers "
        "already carry `require_project_content_role`, so a new router needs "
        "that dependency, and a stricter write gate needs its own "
        "`_require_role(...)`. Then set the catalogue row to "
        "`Auth.ROLE_GATED` with matching `accept_roles`.\n\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(offenders))
    )


def test_the_waiver_list_is_still_empty() -> None:
    """Guards the waiver itself against quiet reintroduction.

    The list went from 69 entries to zero. Re-adding one would make the
    test above pass while the exposure returned — so the emptiness is
    asserted separately from the scan, and shrinking it is the only
    direction that needs no explanation.
    """
    assert not _KNOWN_UNGATED, (
        "entries were added back to `_KNOWN_UNGATED`. The set was emptied "
        "on 2026-10-06 when the gateway boundary and the in-app router "
        f"floor closed all 70; these are now waived again: "
        f"{sorted(_KNOWN_UNGATED)}"
    )


def test_the_catalogue_is_still_being_read() -> None:
    """Guards against the scan going vacuous.

    With an empty waiver, a renamed enum member or field would make
    `_ungated_project_endpoints()` return nothing and BOTH tests above pass
    while checking nothing at all. This pins that the catalogue still
    reports a plausible number of project-scoped endpoints, and that they
    are gated rather than simply absent.
    """
    project_scoped = [e for e in ENDPOINTS if e.scope == Scope.PROJECT]
    assert len(project_scoped) > 100, (
        "the catalogue no longer reports project-scoped endpoints — a field "
        "or enum was renamed and this whole file is now vacuous"
    )
    gated = [e for e in project_scoped if e.auth == Auth.ROLE_GATED]
    assert len(gated) >= 136, (
        f"only {len(gated)} of {len(project_scoped)} project-scoped "
        "endpoints are ROLE_GATED; 136 were gated on 2026-10-06 and that "
        "number is a ratchet"
    )
