# =============================================================================
# File: test_split_intervals.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_split_intervals.py
# Description: Unit tests for the split exhaustiveness check of R-310-092.
#
#              This is the one guard that prevents a clause being lost when a
#              supplied requirement is split, and the failure it catches is
#              INVISIBLE: a split that drops half a sentence leaves every
#              surviving fragment covered, so the matrix reads green and
#              nothing points at the missing text. The tests therefore work
#              on realistic requirement prose, not on abstract ranges.
#
#              "In full" is read LITERALLY: the intervals must tile the
#              source with no gap at all. The first design of the module
#              permitted gaps free of alphanumerics, so that ", " could
#              belong to neither fragment — and these tests killed it on the
#              first realistic sentence, because ", and " contains a word.
#              The convention that dissolves it: a fragment includes its
#              separators, so a well-formed split is contiguous.
#
# @relation validates:R-310-091
# @relation validates:R-310-092
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c5_requirements.intake.intervals import (
    IntervalError,
    TextInterval,
    covered_text,
    statement_of,
    validate_partition,
)

#: A real agglomerated requirement — three clauses in one sentence, which is
#: exactly the shape R-310-093 splits.
SOURCE = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms, limit the torque ramp to 140 Nm/s, and log the event "
    "to the diagnostic buffer."
)

_TIMING_END = SOURCE.index(", limit")
_RAMP_START = SOURCE.index("limit the torque")
_RAMP_END = SOURCE.index(", and log")
_LOG_START = SOURCE.index("log the event")


def _good_split() -> tuple[TextInterval, ...]:
    """The three clauses, each fragment carrying its own leading separator.

    Contiguous by construction: `a.end == b.start` throughout, so the
    separators belong to a fragment rather than to a gap.
    """
    return (
        TextInterval(0, _TIMING_END),
        TextInterval(_TIMING_END, _RAMP_END),
        TextInterval(_RAMP_END, len(SOURCE)),
    )


@pytest.mark.unit
class TestIntervalConstruction:
    def test_half_open_length(self) -> None:
        assert TextInterval(5, 9).length == 4

    def test_an_empty_interval_is_refused(self) -> None:
        # A fragment covering no text is not a fragment.
        with pytest.raises(IntervalError, match="is empty"):
            TextInterval(5, 5)

    def test_an_inverted_interval_is_refused(self) -> None:
        with pytest.raises(IntervalError, match="is empty"):
            TextInterval(9, 5)

    def test_a_negative_start_is_refused(self) -> None:
        with pytest.raises(IntervalError, match="SHALL be >= 0"):
            TextInterval(-1, 5)

    def test_adjacent_intervals_share_a_boundary(self) -> None:
        # Half-open on purpose: adjacency is `a.end == b.start`, which is
        # where off-by-one gaps stop coming from.
        first, second = TextInterval(0, 10), TextInterval(10, 20)
        assert not first.overlaps(second)

    def test_overlap_detection_is_symmetric(self) -> None:
        a, b = TextInterval(0, 10), TextInterval(5, 15)
        assert a.overlaps(b)
        assert b.overlaps(a)

    def test_slice_returns_the_named_text(self) -> None:
        assert TextInterval(0, 2).slice_of("abcdef") == "ab"


@pytest.mark.unit
class TestAValidSplit:
    def test_a_contiguous_three_clause_split_is_valid(self) -> None:
        report = validate_partition(_good_split(), SOURCE)
        assert report.is_valid is True
        assert report.gaps == ()
        assert report.overlaps == ()

    def test_a_valid_split_reproduces_the_source_exactly(self) -> None:
        # The human-checkable counterpart of the arithmetic.
        assert covered_text(_good_split(), SOURCE) == SOURCE

    def test_a_fragment_statement_is_trimmed_for_display(self) -> None:
        # The interval carries the separator for the audit; the statement a
        # reviewer reads does not.
        middle = _good_split()[1]
        assert middle.slice_of(SOURCE).startswith(",")
        assert statement_of(middle, SOURCE).startswith("limit the torque")

    def test_a_single_fragment_covering_everything_is_valid(self) -> None:
        report = validate_partition((TextInterval(0, len(SOURCE)),), SOURCE)
        assert report.is_valid is True

    def test_order_does_not_matter(self) -> None:
        shuffled = tuple(reversed(_good_split()))
        assert validate_partition(shuffled, SOURCE).is_valid is True

    def test_explanation_of_a_valid_split(self) -> None:
        assert "covers the source text exactly" in validate_partition(
            _good_split(), SOURCE
        ).explain()


@pytest.mark.unit
class TestALostClause:
    def test_dropping_the_middle_clause_is_refused(self) -> None:
        """The invisible failure: two fragments, both fine, one clause gone."""
        partial = (
            TextInterval(0, _TIMING_END),
            TextInterval(_LOG_START, len(SOURCE)),
        )
        report = validate_partition(partial, SOURCE)
        assert report.is_valid is False
        assert len(report.gaps) == 1
        assert "limit the torque ramp to 140 Nm/s" in report.gaps[0].text

    def test_the_explanation_quotes_the_lost_text(self) -> None:
        # A reviewer must see WHAT was dropped, not that something was.
        partial = (
            TextInterval(0, _TIMING_END),
            TextInterval(_LOG_START, len(SOURCE)),
        )
        explanation = validate_partition(partial, SOURCE).explain()
        assert "claimed by no fragment" in explanation
        assert "140 Nm/s" in explanation

    def test_dropping_the_tail_is_refused(self) -> None:
        # The commonest real slip: the last clause falls off the end.
        truncated = (
            TextInterval(0, _TIMING_END),
            TextInterval(_TIMING_END, _RAMP_END),
        )
        report = validate_partition(truncated, SOURCE)
        assert report.is_valid is False
        assert "log the event" in report.gaps[0].text

    def test_dropping_the_head_is_refused(self) -> None:
        beheaded = (
            TextInterval(_TIMING_END, _RAMP_END),
            TextInterval(_RAMP_END, len(SOURCE)),
        )
        report = validate_partition(beheaded, SOURCE)
        assert report.is_valid is False
        assert "On brake pedal release" in report.gaps[0].text

    def test_an_empty_split_over_real_text_is_refused(self) -> None:
        # Claiming nothing while the text says something.
        report = validate_partition((), SOURCE)
        assert report.is_valid is False
        assert report.gaps[0].text == SOURCE

    def test_even_a_separator_gap_is_refused(self) -> None:
        """The design reversal, pinned.

        A two-character gap of ", " was legal under the first design. It is
        not now: once "insignificant gap" exists as a notion, somebody has to
        decide what qualifies, and that decision is where a lost clause
        hides.
        """
        text = "The system shall brake, and shall log."
        split = (
            TextInterval(0, text.index(",")),
            TextInterval(text.index("and shall log"), len(text)),
        )
        report = validate_partition(split, text)
        assert report.is_valid is False
        assert report.gaps[0].text == ", "


@pytest.mark.unit
class TestOverlaps:
    def test_two_fragments_claiming_the_same_text_is_refused(self) -> None:
        overlapping = (
            TextInterval(0, _RAMP_END),
            TextInterval(_RAMP_START, len(SOURCE)),
        )
        report = validate_partition(overlapping, SOURCE)
        assert report.is_valid is False
        assert len(report.overlaps) == 1
        shared = report.overlaps[0].shared
        assert shared.start == _RAMP_START
        assert shared.end == _RAMP_END

    def test_a_fully_contained_fragment_is_an_overlap(self) -> None:
        nested = (TextInterval(0, len(SOURCE)), TextInterval(10, 20))
        report = validate_partition(nested, SOURCE)
        assert report.is_valid is False
        assert report.overlaps[0].shared == TextInterval(10, 20)

    def test_the_explanation_names_both_fragments(self) -> None:
        overlapping = (TextInterval(0, 40), TextInterval(20, 60))
        explanation = validate_partition(overlapping, SOURCE).explain()
        assert "both claim" in explanation

    def test_an_overlap_and_a_gap_are_both_reported(self) -> None:
        # The author needs the whole picture, not the first problem found.
        broken = (TextInterval(0, 40), TextInterval(20, 50))
        report = validate_partition(broken, SOURCE)
        assert report.overlaps
        assert report.gaps


@pytest.mark.unit
class TestBounds:
    def test_running_past_the_source_is_refused(self) -> None:
        report = validate_partition(
            (TextInterval(0, len(SOURCE) + 10),), SOURCE
        )
        assert report.is_valid is False
        assert report.out_of_bounds
        assert "runs past the source text" in report.explain()

    def test_exactly_reaching_the_end_is_fine(self) -> None:
        assert validate_partition(
            (TextInterval(0, len(SOURCE)),), SOURCE
        ).is_valid is True


@pytest.mark.unit
class TestCoveredText:
    def test_covered_text_is_the_claimed_prose_in_order(self) -> None:
        # The readable counterpart of the arithmetic: what the split keeps.
        kept = covered_text(_good_split(), SOURCE)
        assert kept.startswith("On brake pedal release")
        assert "140 Nm/s" in kept
        assert "diagnostic buffer" in kept

    def test_covered_text_of_a_lossy_split_is_visibly_short(self) -> None:
        lossy = (TextInterval(0, _TIMING_END),)
        assert len(covered_text(lossy, SOURCE)) < len(SOURCE)
