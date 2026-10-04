# =============================================================================
# File: test_object_repository.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_object_repository.py
# Description: Unit tests for the pure projection layer of ObjectRepository —
#              key construction, content hashing, and the index row shape.
#              The AQL and collection operations are exercised against a real
#              ArangoDB at the integration tier; mocking a database here would
#              only assert that python-arango was called, which proves nothing.
#
#              Load-bearing assertion: the index row carries NO object body
#              (R-310-002). An index holding content would become a second
#              source of truth able to drift from MinIO.
#
# @relation validates:R-310-002
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ay_platform_core.c5_requirements.objects.models import (
    DocObject,
    ObjectType,
    ProducedBy,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
)
from ay_platform_core.c5_requirements.objects.repository import (
    content_hash,
    index_key,
    to_index_row,
)

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)
PID = "adas-brake-ctrl"
CONTAINER = "030-ARCHITECTURE-DESIGN"


def _object(**overrides: object) -> DocObject:
    payload: dict[str, object] = {
        "object_id": "OBJ-1120",
        "project_id": PID,
        "container": CONTAINER,
        "type": ObjectType.PARAGRAPH,
        "body": "The primary actuator shall deliver up to 140 Nm.",
        "ordinal": 3,
        "version": 1,
        "review_state": ReviewState.PROPOSED,
        "created_at": NOW,
        "created_by": "agent:architect",
        "updated_at": NOW,
        "updated_by": "agent:architect",
    }
    payload.update(overrides)
    return DocObject.model_validate(payload)


@pytest.mark.unit
class TestIndexKey:
    def test_key_is_project_scoped(self) -> None:
        # E-310-006: `<project-id>:<object-id>`. Object ids are therefore
        # unique per project, not per container — consistent with R-310-005,
        # under which an identifier is never reused.
        assert index_key(PID, "OBJ-1120") == f"{PID}:OBJ-1120"

    def test_same_id_in_two_projects_yields_distinct_keys(self) -> None:
        assert index_key("p1", "OBJ-1") != index_key("p2", "OBJ-1")


@pytest.mark.unit
class TestContentHash:
    def test_hash_is_stable_for_identical_content(self) -> None:
        assert content_hash(_object()) == content_hash(_object())

    def test_hash_changes_with_the_body(self) -> None:
        assert content_hash(_object()) != content_hash(_object(body="something else"))

    def test_hash_covers_the_notation_for_figures(self) -> None:
        a = _object(type=ObjectType.FIGURE, body=None, notation="graph LR; A-->B")
        b = _object(type=ObjectType.FIGURE, body=None, notation="graph LR; A-->C")
        assert content_hash(a) != content_hash(b)

    def test_hash_is_prefixed_with_its_algorithm(self) -> None:
        digest = content_hash(_object())
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_metadata_alone_does_not_change_the_content_hash(self) -> None:
        # The digest answers "did the content drift?", not "did anything
        # change?" — a re-ordering is not a content change.
        assert content_hash(_object(ordinal=3)) == content_hash(_object(ordinal=9))


@pytest.mark.unit
class TestIndexRow:
    def test_row_never_carries_the_body(self) -> None:
        # R-310-002: the index is derived. Storing content here would create a
        # second place where it can drift from the source of truth.
        row = to_index_row(_object())
        assert "body" not in row
        assert "notation" not in row

    def test_row_never_carries_a_figure_notation(self) -> None:
        row = to_index_row(
            _object(type=ObjectType.FIGURE, body=None, notation="graph LR; A-->B")
        )
        assert "notation" not in row
        assert row["content_hash"].startswith("sha256:")

    def test_row_carries_the_hot_path_fields(self) -> None:
        row = to_index_row(_object())
        for field in ("project_id", "container", "ordinal", "review_state", "version"):
            assert field in row, field

    def test_row_key_matches_index_key(self) -> None:
        assert to_index_row(_object())["_key"] == index_key(PID, "OBJ-1120")

    def test_enums_are_projected_as_wire_values(self) -> None:
        row = to_index_row(_object())
        assert row["type"] == "paragraph"
        assert row["review_state"] == "proposed"

    def test_auto_accepted_is_indexed_distinctly(self) -> None:
        # R-310-007: the review queue and the coverage figures both filter on
        # this field, so collapsing it into "accepted" here would make the
        # auto-accepted share unrecoverable.
        row = to_index_row(_object(review_state=ReviewState.AUTO_ACCEPTED))
        assert row["review_state"] == "auto-accepted"

    def test_provenance_is_flattened_for_filtering(self) -> None:
        row = to_index_row(
            _object(produced_by=ProducedBy(workflow="WF-002", workflow_version=3))
        )
        assert row["produced_by_workflow"] == "WF-002"
        assert row["produced_by_version"] == 3

    def test_absent_provenance_is_null_not_missing(self) -> None:
        # AQL filters on a missing attribute behave differently from filters
        # on null; keeping the key present makes the query predictable.
        row = to_index_row(_object())
        assert row["produced_by_workflow"] is None
        assert row["produced_by_version"] is None

    def test_decision_attribution_is_indexed(self) -> None:
        row = to_index_row(
            _object(
                version=2,
                review_state=ReviewState.ACCEPTED,
                last_review=ReviewRecord(
                    decision=ReviewDecision.CONFIRM_UNCHANGED,
                    actor="m.roche",
                    at=NOW,
                    justification="Ramp limit unaffected by the timing edit.",
                ),
            )
        )
        assert row["last_decision"] == "confirm-unchanged"
        assert row["last_decision_actor"] == "m.roche"

    def test_timestamps_are_iso_strings(self) -> None:
        row = to_index_row(_object())
        assert row["created_at"] == NOW.isoformat()
        assert row["updated_at"] == NOW.isoformat()
