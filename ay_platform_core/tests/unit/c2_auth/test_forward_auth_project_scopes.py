# =============================================================================
# File: test_forward_auth_project_scopes.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_forward_auth_project_scopes.py
# Description: Unit tests for the `X-Project-Scopes` wire format (inc3b).
#
#              The serialiser and the parser sit either side of a process
#              boundary — C2 writes the header, C6/C9/C3/C4 read it. A drift
#              between them does not raise: it yields an empty role set, and
#              the symptom is a 403 in production that no test reproduces.
#              So the round-trip is tested as one property, and the
#              accessor's refusal to generalise across projects is tested as
#              a security property rather than left to the docstring.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c2_auth.forward_auth import (
    parse_project_scopes,
    roles_for_project,
    serialize_project_scopes,
)
from ay_platform_core.c2_auth.models import RBACProjectRole

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Round-trip — the property that keeps the two halves from drifting
# ---------------------------------------------------------------------------


def test_round_trip_preserves_every_project_and_role() -> None:
    scopes = {
        "demo": [RBACProjectRole.OWNER, RBACProjectRole.EDITOR],
        "other": [RBACProjectRole.VIEWER],
    }
    parsed = parse_project_scopes(serialize_project_scopes(scopes))
    assert parsed == {
        "demo": {"project_owner", "project_editor"},
        "other": {"project_viewer"},
    }


def test_serialisation_is_deterministic() -> None:
    """Stable output so the header is comparable and diffable.

    Two equal maps built in different insertion orders SHALL render
    identically — otherwise a test asserting on the header value passes or
    fails by dict ordering.
    """
    a = serialize_project_scopes({
        "b": [RBACProjectRole.EDITOR, RBACProjectRole.OWNER],
        "a": [RBACProjectRole.VIEWER],
    })
    b = serialize_project_scopes({
        "a": [RBACProjectRole.VIEWER],
        "b": [RBACProjectRole.OWNER, RBACProjectRole.EDITOR],
    })
    assert a == b
    assert a == "a=project_viewer;b=project_editor,project_owner"


def test_an_empty_map_serialises_to_an_empty_string() -> None:
    """A legitimate value: a tenant_manager holds no project scope."""
    assert serialize_project_scopes({}) == ""


def test_a_project_with_no_roles_is_omitted() -> None:
    """Rendering `pid=` would produce a group the parser must then discard."""
    assert serialize_project_scopes({"demo": []}) == ""


# ---------------------------------------------------------------------------
# Parsing is total — this value arrives from another process on a hot path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "   ",
        "garbage-without-equals",
        "=project_owner",  # empty project id
        "demo=",  # no roles
        ";;;",
        "demo",
    ],
)
def test_malformed_headers_yield_no_scopes_rather_than_raising(
    header: str | None,
) -> None:
    """Fail CLOSED, never 500.

    A route whose middleware was misconfigured would let a caller put
    arbitrary text here. Raising would hand them a trivial way to error the
    service; returning no scopes denies them instead.
    """
    assert parse_project_scopes(header) == {}


def test_an_unknown_role_name_is_kept_as_a_string_and_simply_fails_to_match()\
        -> None:
    """Forward compatibility that fails closed.

    Roles are parsed as plain strings, so a role a future C2 introduces does
    not crash an older consumer — it just does not match that consumer's
    `required` tuple.
    """
    parsed = parse_project_scopes("demo=project_auditor")
    assert parsed == {"demo": {"project_auditor"}}
    assert "project_owner" not in parsed["demo"]


def test_whitespace_around_separators_is_tolerated() -> None:
    assert parse_project_scopes(" demo = project_owner , project_editor ") == {
        "demo": {"project_owner", "project_editor"}
    }


def test_a_repeated_project_merges_rather_than_overwrites() -> None:
    assert parse_project_scopes("demo=project_viewer;demo=project_owner") == {
        "demo": {"project_viewer", "project_owner"}
    }


# ---------------------------------------------------------------------------
# The accessor — a security property, not a convenience
# ---------------------------------------------------------------------------


def test_roles_are_returned_only_for_the_project_asked_about() -> None:
    header = "demo=project_owner;victim=project_viewer"
    assert roles_for_project(header, "demo") == {"project_owner"}
    assert roles_for_project(header, "victim") == {"project_viewer"}


def test_a_role_on_one_project_grants_nothing_on_another() -> None:
    """The confused-deputy case, pinned.

    A caller who owns `demo` must come back empty-handed for `victim`. If
    this ever returned a union, an MCP tool could trigger a run against any
    project in the tenant on the strength of a scope held on one.
    """
    header = "demo=project_owner,project_editor"
    assert roles_for_project(header, "victim") == set()


def test_an_unknown_project_yields_an_empty_set() -> None:
    assert roles_for_project("demo=project_owner", "never-heard-of-it") == set()


def test_an_empty_project_id_yields_an_empty_set() -> None:
    """Guards against `roles_for_project(header, "")` accidentally matching
    a malformed group and granting something."""
    assert roles_for_project("=project_owner", "") == set()
    assert roles_for_project("demo=project_owner", "") == set()


def test_no_header_yields_an_empty_set() -> None:
    assert roles_for_project(None, "demo") == set()
