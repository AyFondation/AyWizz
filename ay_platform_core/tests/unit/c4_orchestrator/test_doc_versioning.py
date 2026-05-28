# =============================================================================
# File: test_doc_versioning.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_doc_versioning.py
# Description: Unit tests for the live-docs per-file version helpers
#              (D-015 / R-200-147). Covers the commit-message tagging
#              (`_docgen_commit_message`) and the per-AI-response batching
#              of the version count (`_version_from_commit_messages`).
#              These are the pure core of the feature ; the end-to-end
#              wiring (X-Turn-Id → commit → tree) is exercised by the C4
#              integration tests.
#
# @relation validates:R-200-147
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c4_orchestrator.artifacts_service import (
    _docgen_commit_message,
    _version_from_commit_messages,
)

pytestmark = pytest.mark.unit


class TestDocgenCommitMessage:
    def test_appends_turn_marker_when_id_present(self) -> None:
        msg = _docgen_commit_message("docs/intro.md", "turn-abc")
        assert msg == "docgen — docs/intro.md [turn:turn-abc]"

    def test_no_marker_when_turn_id_absent(self) -> None:
        assert _docgen_commit_message("docs/intro.md", None) == "docgen — docs/intro.md"

    def test_empty_turn_id_is_treated_as_absent(self) -> None:
        # A falsy id (e.g. "") MUST NOT emit a `[turn:]` marker that
        # would parse back to an empty turn and skew the count.
        assert _docgen_commit_message("a.md", "") == "docgen — a.md"


class TestVersionFromCommitMessages:
    def test_empty_history_is_none(self) -> None:
        # No commits at all → no badge in the UX.
        assert _version_from_commit_messages([]) is None

    def test_single_tagged_commit_is_v1(self) -> None:
        assert _version_from_commit_messages(["docgen — a.md [turn:t1]"]) == 1

    def test_multiple_writes_same_response_collapse_to_one(self) -> None:
        # Two writes to the same file within ONE AI response share the
        # turn id → one version bump (the feature's core requirement).
        messages = [
            "docgen — a.md [turn:t1]",
            "docgen — a.md [turn:t1]",
        ]
        assert _version_from_commit_messages(messages) == 1

    def test_distinct_responses_count_separately(self) -> None:
        messages = [
            "docgen — a.md [turn:t3]",
            "docgen — a.md [turn:t2]",
            "docgen — a.md [turn:t1]",
        ]
        assert _version_from_commit_messages(messages) == 3

    def test_mixed_repeats_and_distinct(self) -> None:
        # Response t1 wrote twice, t2 once, t3 once → 3 distinct turns.
        messages = [
            "docgen — a.md [turn:t3]",
            "docgen — a.md [turn:t2]",
            "docgen — a.md [turn:t1]",
            "docgen — a.md [turn:t1]",
        ]
        assert _version_from_commit_messages(messages) == 3

    def test_untagged_history_falls_back_to_v1(self) -> None:
        # Legacy / operator-driven commits carry no turn marker. An
        # existing file still reads as at least v1 rather than v0.
        messages = [
            "docgen — a.md",
            "docgen — rename a.md -> b.md",
        ]
        assert _version_from_commit_messages(messages) == 1

    def test_tagged_and_untagged_mixed_counts_only_tags(self) -> None:
        # Untagged commits do not add versions ; the count reflects the
        # distinct AI responses (here: t1, t2 → v2). The untagged
        # structural-op commit does not bump the version.
        messages = [
            "docgen — a.md [turn:t2]",
            "docgen — move a.md",
            "docgen — a.md [turn:t1]",
        ]
        assert _version_from_commit_messages(messages) == 2
