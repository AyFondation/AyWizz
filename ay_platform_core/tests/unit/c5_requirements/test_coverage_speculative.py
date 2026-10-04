# =============================================================================
# File: test_coverage_speculative.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_coverage_speculative.py
# Description: The speculative provenance marking — R-310-177 v2.
#
#              THE TEST THAT CARRIES THE DESIGN is the one asserting an
#              object can be `accepted` AND speculative at once. That is the
#              situation end-to-end execution routinely produces, and it is
#              the reason `speculative` is orthogonal to the review state
#              rather than a sixth member of it.
#
# @relation validates:R-310-177
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.coverage.models import (
    ACCEPTED_STATES,
    SpeculativeListResponse,
    SpeculativeMarking,
)
from ay_platform_core.c5_requirements.objects.models import ReviewState


def _marking(**overrides: object) -> SpeculativeMarking:
    base: dict[str, object] = {
        "object_id": "AD-100",
        "container": "030-ARCH",
        "unaccepted_targets": ("FD-010",),
    }
    base.update(overrides)
    return SpeculativeMarking.model_validate(base)


# ---------------------------------------------------------------------------
# Orthogonality — the whole reason for the design
# ---------------------------------------------------------------------------


def test_speculative_is_not_a_member_of_the_review_state_set() -> None:
    """R-310-006 keeps its five states; R-310-177 adds no sixth."""
    assert "speculative" not in {state.value for state in ReviewState}
    assert {state.value for state in ReviewState} == {
        "proposed",
        "accepted",
        "auto-accepted",
        "stale",
        "rejected",
    }


def test_an_object_can_be_accepted_and_speculative_at_once() -> None:
    """The situation end-to-end execution produces, and a sixth state forbids.

    A reviewer accepts the architecture object while the functional object
    above it is still `proposed`. Both facts must be recordable.
    """
    marking = _marking()
    reviewed_state = ReviewState.ACCEPTED
    assert marking.is_speculative is True
    assert reviewed_state in ACCEPTED_STATES


def test_both_acceptance_states_count_as_accepted_foundations() -> None:
    """R-310-007 keeps them distinct for COUNTING; both are still acceptance."""
    assert ReviewState.ACCEPTED in ACCEPTED_STATES
    assert ReviewState.AUTO_ACCEPTED in ACCEPTED_STATES
    assert len(ACCEPTED_STATES) == 2


def test_a_proposed_upstream_is_not_an_accepted_foundation() -> None:
    for state in (ReviewState.PROPOSED, ReviewState.STALE, ReviewState.REJECTED):
        assert state not in ACCEPTED_STATES


# ---------------------------------------------------------------------------
# The marking itself
# ---------------------------------------------------------------------------


def test_no_unaccepted_upstream_means_not_speculative() -> None:
    marking = _marking(unaccepted_targets=())
    assert marking.is_speculative is False
    assert "every upstream it answers is accepted" in marking.explain()


def test_an_unaccepted_upstream_makes_it_speculative() -> None:
    marking = _marking()
    assert marking.is_speculative is True
    assert "nobody has accepted" in marking.explain()
    assert "FD-010" in marking.explain()


def test_an_upstream_that_has_not_moved_does_not_force_staleness() -> None:
    """Built on sand is not the same as built on sand that shifted."""
    marking = _marking()
    assert marking.must_become_stale is False


def test_an_unaccepted_upstream_that_changed_forces_staleness() -> None:
    """R-310-177's second clause: it answers a version that no longer exists."""
    marking = _marking(advanced_targets=("FD-010",))
    assert marking.must_become_stale is True
    assert "changed before acceptance" in marking.explain()
    assert "R-310-177" in marking.explain()


def test_the_explanation_names_the_container() -> None:
    assert "030-ARCH" in _marking().explain()


def test_several_unaccepted_upstreams_are_all_named() -> None:
    marking = _marking(unaccepted_targets=("FD-010", "FD-011"))
    assert "FD-010" in marking.explain()
    assert "FD-011" in marking.explain()


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        SpeculativeMarking.model_validate(
            {
                "object_id": "AD-100",
                "container": "030-ARCH",
                "review_state": "speculative",
            }
        )


def test_a_marking_round_trips_through_its_serialised_form() -> None:
    marking = _marking(advanced_targets=("FD-010",))
    assert SpeculativeMarking.model_validate(marking.model_dump()) == marking


# ---------------------------------------------------------------------------
# The listing
# ---------------------------------------------------------------------------


def test_the_listing_separates_what_must_be_looked_at_now() -> None:
    response = SpeculativeListResponse(
        markings=(_marking(), _marking(object_id="AD-101", advanced_targets=("FD-010",))),
        count=2,
        stale_count=1,
    )
    assert response.count == 2
    assert response.stale_count == 1


def test_an_empty_listing_is_representable() -> None:
    response = SpeculativeListResponse(markings=(), count=0)
    assert response.count == 0
    assert response.stale_count == 0
