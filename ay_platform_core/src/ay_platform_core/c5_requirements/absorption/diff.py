# =============================================================================
# File: diff.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/diff.py
# Description: The supplied-set diff — 310-SPEC §4.8 (R-310-140).
#
#              EXACTLY ONE CHANGE PER REQUIREMENT. R-310-140 says one
#              ticket per added, modified or removed requirement, so the
#              diff is keyed by requirement id and a duplicated id in
#              either side is refused rather than silently collapsed: two
#              records claiming the same identifier means the drop is
#              malformed, and picking one of them would decide which half
#              of the customer's intent to honour.
#
#              WHAT COUNTS AS MODIFIED — the one real judgement here, and
#              it is bounded by the spec to the three kinds above. A
#              re-export from the issuer's tooling commonly reflows every
#              paragraph, so comparing raw bytes would open thousands of
#              tickets whose content is identical. A flood of tickets is
#              not a conservative outcome: nothing in it gets reviewed
#              properly, which is a worse safety result than missing a
#              reflow. So comparison is on whitespace-normalised text,
#              and the raw before/after is carried anyway so a reviewer
#              sees what actually arrived. The normaliser is a parameter:
#              a corpus where whitespace is load-bearing (tables, code)
#              passes `identity` and gets byte comparison back.
#
#              WHAT THE PREVIOUS SET IS. R-310-140 says "the current
#              baseline". Baselines land in increment 8, so `previous` is
#              an argument rather than a lookup: today the service feeds
#              it the stored supplied requirements, later it feeds it a
#              baseline manifest, and this module does not change either
#              way (DV-21).
#
# @relation implements:R-310-140
# =============================================================================

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum

_WHITESPACE = re.compile(r"\s+")


class ChangeKind(StrEnum):
    """The three outcomes a diff may produce (`R-310-140`).

    Closed on purpose: a fourth kind would be a spec amendment, not an
    implementation choice.
    """

    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"


class DiffError(RuntimeError):
    """Raised when a side of the diff cannot be keyed by requirement id."""


def collapse_whitespace(text: str) -> str:
    """Return `text` with runs of whitespace collapsed and ends stripped.

    The default comparison basis: it ignores the reflow an issuer's
    exporter introduces while still seeing every added, removed or
    reordered token.
    """
    return _WHITESPACE.sub(" ", text).strip()


def identity(text: str) -> str:
    """Return `text` unchanged — byte-exact comparison.

    For corpora where whitespace carries meaning.
    """
    return text


@dataclass(frozen=True, slots=True)
class RequirementSnapshot:
    """One supplied requirement as one side of the diff sees it."""

    requirement_id: str
    text: str


@dataclass(frozen=True, slots=True)
class SuppliedChange:
    """One added, modified or removed requirement.

    Attributes:
        requirement_id: The identifier the change is about.
        kind: Which of the three outcomes occurred.
        previous_text: What the baseline held; None when added.
        current_text: What arrived; None when removed.
    """

    requirement_id: str
    kind: ChangeKind
    previous_text: str | None
    current_text: str | None

    @property
    def is_reformatting_only(self) -> bool:
        """True when the raw texts differ but the compared forms did not.

        Always False for a change this module emitted — it exists so a
        caller can assert that, and so the property has a name when the
        question is asked of a stored change.
        """
        if self.previous_text is None or self.current_text is None:
            return False
        return self.previous_text != self.current_text and collapse_whitespace(
            self.previous_text
        ) == collapse_whitespace(self.current_text)


@dataclass(frozen=True, slots=True)
class SourceDiff:
    """What one re-supplied source changed against the previous set."""

    changes: tuple[SuppliedChange, ...]
    unchanged_ids: tuple[str, ...]
    reformatted_ids: tuple[str, ...]
    """Requirements whose raw text changed but whose compared form did
    not. Reported rather than dropped: no ticket is opened for them, and
    a reviewer who suspects whitespace matters here can see which ones."""

    @property
    def is_empty(self) -> bool:
        """True when the source arrived materially unchanged."""
        return not self.changes

    @property
    def change_count(self) -> int:
        """How many tickets this diff will open."""
        return len(self.changes)

    def of_kind(self, kind: ChangeKind) -> tuple[SuppliedChange, ...]:
        """Return the changes of one kind."""
        return tuple(change for change in self.changes if change.kind is kind)

    def change(self, requirement_id: str) -> SuppliedChange:
        """Return the change for one requirement.

        Raises:
            KeyError: When that requirement did not change.
        """
        for change in self.changes:
            if change.requirement_id == requirement_id:
                return change
        raise KeyError(f"{requirement_id!r} did not change")


def diff_supplied(
    previous: Iterable[RequirementSnapshot],
    current: Iterable[RequirementSnapshot],
    *,
    compare: Callable[[str], str] = collapse_whitespace,
) -> SourceDiff:
    """Diff a re-supplied source against the previous set.

    Args:
        previous: The baseline side.
        current: What just arrived.
        compare: Normaliser applied before comparing texts. Defaults to
            whitespace collapsing; pass `identity` for byte comparison.

    Returns:
        Exactly one change per added, modified or removed requirement.

    Raises:
        DiffError: When either side repeats a requirement id.
    """
    before = _index(previous, "previous")
    after = _index(current, "current")

    changes: list[SuppliedChange] = []
    unchanged: list[str] = []
    reformatted: list[str] = []

    for requirement_id in sorted(set(before) | set(after)):
        was = before.get(requirement_id)
        now = after.get(requirement_id)
        if was is None:
            changes.append(
                SuppliedChange(
                    requirement_id=requirement_id,
                    kind=ChangeKind.ADDED,
                    previous_text=None,
                    current_text=_text(now),
                )
            )
        elif now is None:
            changes.append(
                SuppliedChange(
                    requirement_id=requirement_id,
                    kind=ChangeKind.REMOVED,
                    previous_text=was.text,
                    current_text=None,
                )
            )
        elif compare(was.text) != compare(now.text):
            changes.append(
                SuppliedChange(
                    requirement_id=requirement_id,
                    kind=ChangeKind.MODIFIED,
                    previous_text=was.text,
                    current_text=now.text,
                )
            )
        else:
            unchanged.append(requirement_id)
            if was.text != now.text:
                reformatted.append(requirement_id)

    return SourceDiff(
        changes=tuple(changes),
        unchanged_ids=tuple(unchanged),
        reformatted_ids=tuple(reformatted),
    )


def _index(
    snapshots: Iterable[RequirementSnapshot], side: str
) -> dict[str, RequirementSnapshot]:
    """Key one side by requirement id, refusing a repeat.

    Raises:
        DiffError: When an identifier appears twice.
    """
    indexed: dict[str, RequirementSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.requirement_id in indexed:
            raise DiffError(
                f"{snapshot.requirement_id!r} appears twice on the {side} side; "
                "R-310-140 opens exactly one ticket per requirement, and "
                "choosing between two records under one identifier would "
                "decide which half of the issuer's intent to honour"
            )
        indexed[snapshot.requirement_id] = snapshot
    return indexed


def _text(snapshot: RequirementSnapshot | None) -> str:
    """Return a present snapshot's text.

    Raises:
        DiffError: When called with None, which the caller's branching
            already excludes.
    """
    if snapshot is None:  # pragma: no cover - excluded by the caller's branch
        raise DiffError("missing snapshot")
    return snapshot.text
