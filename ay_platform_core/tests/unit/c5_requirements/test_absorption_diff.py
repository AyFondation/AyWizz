# =============================================================================
# File: test_absorption_diff.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_absorption_diff.py
# Description: The supplied-set diff — R-310-140.
#
# @relation validates:R-310-140
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c5_requirements.absorption.diff import (
    ChangeKind,
    DiffError,
    RequirementSnapshot,
    collapse_whitespace,
    diff_supplied,
    identity,
)

_BRAKE = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms."
)
_BRAKE_FASTER = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 150 ms."
)


def _snap(requirement_id: str, text: str) -> RequirementSnapshot:
    return RequirementSnapshot(requirement_id=requirement_id, text=text)


# ---------------------------------------------------------------------------
# The three kinds — R-310-140
# ---------------------------------------------------------------------------


def test_an_unchanged_source_opens_no_ticket() -> None:
    previous = [_snap("CUST-001", _BRAKE)]
    result = diff_supplied(previous, [_snap("CUST-001", _BRAKE)])
    assert result.is_empty
    assert result.unchanged_ids == ("CUST-001",)


def test_a_modified_requirement_yields_one_change_carrying_both_texts() -> None:
    result = diff_supplied(
        [_snap("CUST-001", _BRAKE)], [_snap("CUST-001", _BRAKE_FASTER)]
    )
    change = result.change("CUST-001")
    assert change.kind is ChangeKind.MODIFIED
    assert change.previous_text == _BRAKE
    assert change.current_text == _BRAKE_FASTER


def test_a_new_requirement_is_added_with_no_previous_text() -> None:
    result = diff_supplied([], [_snap("CUST-002", _BRAKE)])
    change = result.change("CUST-002")
    assert change.kind is ChangeKind.ADDED
    assert change.previous_text is None
    assert change.current_text == _BRAKE


def test_a_withdrawn_requirement_is_removed_with_no_current_text() -> None:
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [])
    change = result.change("CUST-001")
    assert change.kind is ChangeKind.REMOVED
    assert change.previous_text == _BRAKE
    assert change.current_text is None


def test_exactly_one_change_is_emitted_per_requirement() -> None:
    result = diff_supplied(
        [_snap("CUST-001", _BRAKE), _snap("CUST-002", _BRAKE)],
        [_snap("CUST-001", _BRAKE_FASTER), _snap("CUST-003", _BRAKE)],
    )
    assert result.change_count == 3
    assert [change.requirement_id for change in result.changes] == [
        "CUST-001",
        "CUST-002",
        "CUST-003",
    ]
    assert len({change.requirement_id for change in result.changes}) == 3


def test_the_three_kinds_are_separable() -> None:
    result = diff_supplied(
        [_snap("CUST-001", _BRAKE), _snap("CUST-002", _BRAKE)],
        [_snap("CUST-001", _BRAKE_FASTER), _snap("CUST-003", _BRAKE)],
    )
    assert [c.requirement_id for c in result.of_kind(ChangeKind.MODIFIED)] == ["CUST-001"]
    assert [c.requirement_id for c in result.of_kind(ChangeKind.REMOVED)] == ["CUST-002"]
    assert [c.requirement_id for c in result.of_kind(ChangeKind.ADDED)] == ["CUST-003"]


def test_changes_are_ordered_by_identifier_so_two_runs_agree() -> None:
    forward = diff_supplied(
        [], [_snap("CUST-003", _BRAKE), _snap("CUST-001", _BRAKE)]
    )
    backward = diff_supplied(
        [], [_snap("CUST-001", _BRAKE), _snap("CUST-003", _BRAKE)]
    )
    assert forward.changes == backward.changes


# ---------------------------------------------------------------------------
# A repeated identifier is a malformed drop, not a merge decision
# ---------------------------------------------------------------------------


def test_a_repeated_identifier_on_the_incoming_side_is_refused() -> None:
    with pytest.raises(DiffError, match="appears twice on the current side"):
        diff_supplied([], [_snap("CUST-001", _BRAKE), _snap("CUST-001", _BRAKE_FASTER)])


def test_a_repeated_identifier_on_the_baseline_side_is_refused() -> None:
    with pytest.raises(DiffError, match="appears twice on the previous side"):
        diff_supplied([_snap("CUST-001", _BRAKE), _snap("CUST-001", _BRAKE)], [])


def test_the_refusal_explains_why_it_is_not_resolved_silently() -> None:
    with pytest.raises(DiffError, match="R-310-140"):
        diff_supplied([], [_snap("CUST-001", _BRAKE), _snap("CUST-001", _BRAKE)])


# ---------------------------------------------------------------------------
# Reflow is not a modification, and is reported rather than dropped
# ---------------------------------------------------------------------------


def test_a_reflowed_requirement_opens_no_ticket() -> None:
    """A re-export that rewraps every paragraph must not flood the queue."""
    reflowed = _BRAKE.replace(" ", "\n  ", 3)
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [_snap("CUST-001", reflowed)])

    assert result.is_empty
    assert result.unchanged_ids == ("CUST-001",)


def test_a_reflowed_requirement_is_still_named_so_nothing_is_hidden() -> None:
    reflowed = _BRAKE.replace(" ", "   ", 2)
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [_snap("CUST-001", reflowed)])
    assert result.reformatted_ids == ("CUST-001",)


def test_an_identical_requirement_is_not_reported_as_reformatted() -> None:
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [_snap("CUST-001", _BRAKE)])
    assert result.reformatted_ids == ()


def test_byte_comparison_is_available_when_whitespace_carries_meaning() -> None:
    reflowed = _BRAKE.replace(" ", "\n", 1)
    result = diff_supplied(
        [_snap("CUST-001", _BRAKE)],
        [_snap("CUST-001", reflowed)],
        compare=identity,
    )
    assert result.change("CUST-001").kind is ChangeKind.MODIFIED


def test_a_single_removed_word_is_a_modification_despite_normalisation() -> None:
    """Normalisation must not swallow a token.

    The whole safety argument for collapsing whitespace is that it cannot
    hide an added, removed or reordered token — this pins that.
    """
    shortened = _BRAKE.replace(" within 250 ms", "")
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [_snap("CUST-001", shortened)])
    assert result.change("CUST-001").kind is ChangeKind.MODIFIED


def test_reordered_words_are_a_modification() -> None:
    reordered = "The system shall within 250 ms reduce deceleration to zero."
    result = diff_supplied(
        [_snap("CUST-001", "The system shall reduce deceleration to zero within 250 ms.")],
        [_snap("CUST-001", reordered)],
    )
    assert result.change("CUST-001").kind is ChangeKind.MODIFIED


# ---------------------------------------------------------------------------
# Helpers and accessors
# ---------------------------------------------------------------------------


def test_collapse_whitespace_normalises_runs_and_ends() -> None:
    assert collapse_whitespace("  a \n\t b  ") == "a b"


def test_identity_leaves_text_untouched() -> None:
    assert identity("  a \n b ") == "  a \n b "


def test_asking_for_an_unchanged_requirement_is_an_error_not_a_default() -> None:
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [_snap("CUST-001", _BRAKE)])
    with pytest.raises(KeyError, match="did not change"):
        result.change("CUST-001")


def test_a_modification_reports_whether_it_was_only_reformatting() -> None:
    result = diff_supplied(
        [_snap("CUST-001", _BRAKE)],
        [_snap("CUST-001", _BRAKE.replace(" ", "  ", 1))],
        compare=identity,
    )
    assert result.change("CUST-001").is_reformatting_only is True


def test_a_substantive_modification_is_not_reformatting_only() -> None:
    result = diff_supplied(
        [_snap("CUST-001", _BRAKE)], [_snap("CUST-001", _BRAKE_FASTER)]
    )
    assert result.change("CUST-001").is_reformatting_only is False


def test_an_addition_is_never_reformatting_only() -> None:
    result = diff_supplied([], [_snap("CUST-001", _BRAKE)])
    assert result.change("CUST-001").is_reformatting_only is False


def test_a_removal_is_never_reformatting_only() -> None:
    result = diff_supplied([_snap("CUST-001", _BRAKE)], [])
    assert result.change("CUST-001").is_reformatting_only is False


def test_an_empty_diff_on_both_sides_is_empty() -> None:
    result = diff_supplied([], [])
    assert result.is_empty
    assert result.unchanged_ids == ()
    assert result.reformatted_ids == ()
