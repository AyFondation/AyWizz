# =============================================================================
# File: intervals.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/intervals.py
# Description: The exhaustiveness and non-overlap check of R-310-092 — the
#              one guard that prevents a clause being lost when a supplied
#              requirement is split.
#
#              WHY THIS IS ARITHMETIC AND NOT JUDGEMENT. The failure it
#              catches is invisible: a split that silently drops half a
#              sentence leaves every surviving fragment covered, so the
#              coverage matrix reads green and nothing ever points at the
#              missing clause. Expressed as interval arithmetic over the
#              source text, the check needs no model and no reviewer to be
#              correct.
#
#              "IN FULL" IS READ LITERALLY: the intervals SHALL tile the
#              source text with no gap whatsoever. The first design of this
#              module permitted gaps that contained no alphanumeric
#              character, so that the ", " between two clauses could belong
#              to neither — and the first realistic test killed it, because
#              ", and " contains a word. Exempting conjunctions would have
#              meant a stop-word list, i.e. exactly the judgement this check
#              exists to avoid.
#
#              The convention that dissolves the problem instead: a fragment
#              INCLUDES its separators, so the intervals are contiguous and
#              the check needs no interpretation at all. The interval is the
#              audit artefact — it proves nothing was dropped; the fragment's
#              readable statement is a separate field, trimmed for display.
#              They do not have to be the same string.
#
# @relation implements:R-310-091
# @relation implements:R-310-092
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field


class IntervalError(ValueError):
    """Raised when an interval is not a usable span of the source text."""


@dataclass(frozen=True, slots=True, order=True)
class TextInterval:
    """A half-open span `[start, end)` of a source requirement's text.

    Half-open on purpose: adjacent fragments then share a boundary value
    (`a.end == b.start`) instead of differing by one, which is where
    off-by-one gaps come from.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise IntervalError(f"interval start SHALL be >= 0, got {self.start}")
        if self.end <= self.start:
            raise IntervalError(
                f"interval [{self.start}, {self.end}) is empty; a fragment "
                "covering no text is not a fragment"
            )

    @property
    def length(self) -> int:
        """Number of characters the interval spans."""
        return self.end - self.start

    def overlaps(self, other: TextInterval) -> bool:
        """Return True when the two spans share at least one character."""
        return self.start < other.end and other.start < self.end

    def slice_of(self, text: str) -> str:
        """Return the substring this interval names.

        Args:
            text: The source text.

        Returns:
            The spanned substring.
        """
        return text[self.start : self.end]


@dataclass(frozen=True, slots=True)
class Gap:
    """A stretch of source text no fragment claims.

    Every gap invalidates the split. There is deliberately no
    "insignificant gap" notion: the moment one exists, somebody has to
    decide what counts as insignificant, and that decision is where a lost
    clause hides.
    """

    interval: TextInterval
    text: str


@dataclass(frozen=True, slots=True)
class Overlap:
    """Two fragments claiming the same characters."""

    first: TextInterval
    second: TextInterval
    shared: TextInterval


@dataclass(frozen=True, slots=True)
class PartitionReport:
    """The verdict on one proposed split, with its evidence.

    A boolean would make the failure unactionable: the author needs to know
    WHICH text was dropped and WHICH fragments collide, not merely that
    something is wrong.
    """

    source_length: int
    intervals: tuple[TextInterval, ...]
    gaps: tuple[Gap, ...] = field(default_factory=tuple)
    overlaps: tuple[Overlap, ...] = field(default_factory=tuple)
    out_of_bounds: tuple[TextInterval, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        """True when the intervals tile the source exactly, with no overlap."""
        return not (self.gaps or self.overlaps or self.out_of_bounds)

    def explain(self) -> str:
        """Return a reviewer-facing explanation of why the split was refused."""
        if self.is_valid:
            return "split covers the source text exactly"
        parts: list[str] = []
        for gap in self.gaps:
            parts.append(
                f"text at [{gap.interval.start}, {gap.interval.end}) is claimed "
                f"by no fragment: {gap.text!r}"
            )
        for overlap in self.overlaps:
            parts.append(
                f"fragments [{overlap.first.start}, {overlap.first.end}) and "
                f"[{overlap.second.start}, {overlap.second.end}) both claim "
                f"[{overlap.shared.start}, {overlap.shared.end})"
            )
        for bad in self.out_of_bounds:
            parts.append(
                f"interval [{bad.start}, {bad.end}) runs past the source text "
                f"({self.source_length} characters)"
            )
        return "; ".join(parts)


def validate_partition(
    intervals: tuple[TextInterval, ...], source_text: str
) -> PartitionReport:
    """Check that fragment intervals tile the source text (R-310-092).

    Args:
        intervals: The fragments' claimed spans, in any order.
        source_text: The untranslated source requirement text (R-310-091).

    Returns:
        A report carrying the verdict and its evidence. An empty interval
        set over a non-empty source is invalid: it claims nothing while the
        text says something. A fragment includes its separators, so a
        well-formed split leaves no gap at all.
    """
    length = len(source_text)
    ordered = tuple(sorted(intervals))

    out_of_bounds = tuple(i for i in ordered if i.end > length)

    overlaps: list[Overlap] = []
    for index, current in enumerate(ordered):
        for following in ordered[index + 1 :]:
            if not current.overlaps(following):
                # Sorted, so once a successor starts after `current` ends,
                # no later one can overlap it either.
                break
            overlaps.append(
                Overlap(
                    first=current,
                    second=following,
                    shared=TextInterval(
                        start=max(current.start, following.start),
                        end=min(current.end, following.end),
                    ),
                )
            )

    gaps: list[Gap] = []
    cursor = 0
    for interval in ordered:
        if interval.start > cursor:
            span = TextInterval(start=cursor, end=interval.start)
            gaps.append(Gap(interval=span, text=span.slice_of(source_text)))
        cursor = max(cursor, interval.end)
    if cursor < length:
        span = TextInterval(start=cursor, end=length)
        gaps.append(Gap(interval=span, text=span.slice_of(source_text)))

    return PartitionReport(
        source_length=length,
        intervals=ordered,
        gaps=tuple(gaps),
        overlaps=tuple(overlaps),
        out_of_bounds=out_of_bounds,
    )


def covered_text(
    intervals: tuple[TextInterval, ...], source_text: str
) -> str:
    """Return the source text the intervals actually claim, in order.

    The readable counterpart of the arithmetic: on a valid split this
    reproduces the source exactly, which is a second, human-checkable way
    of seeing that nothing was dropped.
    """
    return "".join(i.slice_of(source_text) for i in sorted(intervals))


def statement_of(interval: TextInterval, source_text: str) -> str:
    """Return a fragment's readable statement, trimmed of leading separators.

    The interval must tile the source, so a fragment often starts on a comma
    or a conjunction. That is right for the audit and wrong for a reviewer's
    eye, so display trims it — the stored interval is untouched.
    """
    return interval.slice_of(source_text).strip().lstrip(",;").strip()
