# =============================================================================
# File: test_route_shadowing.py
# Version: 1
# Path: ay_platform_core/tests/coherence/test_route_shadowing.py
# Description: Refuses a route that can never be reached because an earlier
#              route with a path VARIABLE in the same position swallows it.
#
#              WHY THIS EXISTS. Increment 5 of 310-SPEC shipped
#              `GET /changes/impact/{requirement_id}` after
#              `GET /changes/{drop_id}/{requirement_id}`. Starlette matches
#              in declaration order, so every request for the impact preview
#              reached the ticket handler with `drop_id="impact"` — a 404
#              that looks like missing data rather than a routing fault.
#              Caught by hand that time, and the ad-hoc check written
#              afterwards compared only "shapes", which cannot see this
#              class at all: a literal and a variable produce DIFFERENT
#              shapes and so never collide in that comparison.
#
#              The failure is silent, cheap to introduce and expensive to
#              diagnose, which is exactly the profile that belongs in a
#              coherence test rather than in a reviewer's attention.
#
#              WHAT IS AND IS NOT A FINDING. Only a LITERAL shadowed by an
#              EARLIER variable at the same depth is reported. Two variables
#              at the same position are a genuine ambiguity the author
#              resolved by ordering, and a literal declared BEFORE the
#              variable is the correct ordering — neither is a defect.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from tests.coherence.test_route_catalog import _ROUTERS

pytestmark = pytest.mark.coherence


def _is_variable(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _segments(path: str) -> tuple[str, ...]:
    return tuple(path.strip("/").split("/"))


def _shadows(earlier: tuple[str, ...], later: tuple[str, ...]) -> int | None:
    """Return the position at which `earlier` swallows `later`, or None.

    `earlier` shadows `later` when it matches every concrete path `later`
    would: same length, and at every position either the two segments are
    identical or `earlier` holds a variable where `later` holds a literal.

    A variable in `later` where `earlier` has a literal does NOT shadow —
    `later` then accepts paths `earlier` refuses, so it is still reachable.
    """
    if len(earlier) != len(later):
        return None
    shadowed_at: int | None = None
    for index, (before, after) in enumerate(zip(earlier, later, strict=True)):
        if before == after:
            continue
        if _is_variable(before) and not _is_variable(after):
            # `earlier` is more general here: it absorbs this literal.
            if shadowed_at is None:
                shadowed_at = index
            continue
        return None
    return shadowed_at


def _declared_routes() -> list[tuple[str, str, str, int]]:
    """Return `(component, method, path, declaration_index)` per route.

    The index is per router, because Starlette resolves within the router
    the request was dispatched to.
    """
    found: list[tuple[str, str, str, int]] = []
    for component, router, prefix in _ROUTERS:
        for index, route in enumerate(router.routes):  # type: ignore[attr-defined]
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods or set()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                found.append((component, method, f"{prefix}{route.path}", index))
    return found


def test_no_route_is_shadowed_by_an_earlier_variable_route() -> None:
    """A literal route declared after a variable route that absorbs it is a bug.

    The fix is NOT to reorder: an ordering-dependent surface breaks again
    the next time someone sorts the file. Give the literal route its own
    segment so no two routes in the module share a resolvable shape.
    """
    routes = _declared_routes()
    findings: list[str] = []

    for component, method, path, index in routes:
        later = _segments(path)
        for other_component, other_method, other_path, other_index in routes:
            if other_component != component or other_method != method:
                continue
            if other_index >= index or other_path == path:
                continue
            position = _shadows(_segments(other_path), later)
            if position is None:
                continue
            findings.append(
                f"{component} {method} {path}\n"
                f"      is unreachable: {other_path}\n"
                f"      is declared earlier (index {other_index} < {index}) and its "
                f"variable at segment {position} absorbs "
                f"{later[position]!r}"
            )

    assert not findings, (
        "Route(s) that can never be reached — an earlier route with a path "
        "variable in the same position matches every request they would "
        "serve. Starlette resolves in declaration order, so the later route "
        "is dead code and its callers get the WRONG handler, usually "
        "presenting as a 404 with a nonsense path parameter.\n\n"
        "Do not fix by reordering: that breaks again the next time the file "
        "is sorted. Give the literal route its own path segment.\n\n  "
        + "\n\n  ".join(findings)
    )


# ---------------------------------------------------------------------------
# The detector itself, so a false negative cannot hide behind a green test
# ---------------------------------------------------------------------------


def test_a_variable_absorbing_a_later_literal_is_detected() -> None:
    """The increment 5 bug, in its minimal form."""
    earlier = _segments("/changes/{drop_id}/{requirement_id}")
    later = _segments("/changes/impact/{requirement_id}")
    assert _shadows(earlier, later) == 1


def test_a_literal_declared_before_a_variable_is_not_a_finding() -> None:
    """That is the CORRECT ordering, not a defect."""
    earlier = _segments("/changes/impact/{requirement_id}")
    later = _segments("/changes/{drop_id}/{requirement_id}")
    assert _shadows(earlier, later) is None


def test_two_variables_at_one_position_are_not_a_finding() -> None:
    """A genuine ambiguity the author resolved by ordering."""
    earlier = _segments("/plans/{plan_id}/steps")
    later = _segments("/plans/{other_id}/steps")
    assert _shadows(earlier, later) is None


def test_paths_of_different_length_never_shadow() -> None:
    earlier = _segments("/changes/{drop_id}")
    later = _segments("/changes/impact/{requirement_id}")
    assert _shadows(earlier, later) is None


def test_distinct_literals_never_shadow() -> None:
    assert _shadows(_segments("/plans/suspect"), _segments("/plans/speculative")) is None


def test_an_identical_path_is_not_reported_as_shadowing_itself() -> None:
    same = _segments("/plans/{plan_id}")
    assert _shadows(same, same) is None


def test_shadowing_is_reported_at_the_first_absorbed_position() -> None:
    earlier = _segments("/a/{x}/{y}/d")
    later = _segments("/a/b/c/d")
    assert _shadows(earlier, later) == 1


def test_the_platform_has_routes_to_check() -> None:
    """Guards against the suite passing because it inspected nothing."""
    assert len(_declared_routes()) > 200
