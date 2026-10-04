# =============================================================================
# File: test_object_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_object_models.py
# Description: Unit tests for the object-grain document model of
#              310-SPEC-DOC-TRACEABILITY §4.1. Each test names the
#              requirement it validates. The two load-bearing invariants
#              are figure/prose exclusivity (R-310-008) and
#              decision-triggered versioning (R-310-010).
#
# @relation validates:R-310-004
# @relation validates:R-310-005
# @relation validates:R-310-006
# @relation validates:R-310-007
# @relation validates:R-310-008
# @relation validates:R-310-009
# @relation validates:R-310-010
# @relation validates:R-310-190
# @relation validates:R-310-191
# @relation validates:R-310-193
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.objects.models import (
    DocObject,
    DocObjectPublic,
    HolderKind,
    ObjectLock,
    ObjectReviewRequest,
    ObjectType,
    ProducedBy,
    ReviewDecision,
    ReviewRecord,
    ReviewState,
    WorkingDraft,
    advances_version,
    is_valid_object_id,
    state_for_decision,
)

NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)


def _accept_record(**overrides: object) -> ReviewRecord:
    payload: dict[str, object] = {
        "decision": ReviewDecision.ACCEPT,
        "actor": "o.mathieu",
        "at": NOW,
    }
    payload.update(overrides)
    return ReviewRecord.model_validate(payload)


def _object(**overrides: object) -> DocObject:
    payload: dict[str, object] = {
        "object_id": "OBJ-1120",
        "project_id": "adas-brake-ctrl",
        "container": "030-ARCHITECTURE-DESIGN",
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
class TestObjectIdentity:
    """R-310-005 — identifiers are stable, uppercase, hyphen-segmented."""

    def test_conventional_ids_accepted(self) -> None:
        for oid in ("OBJ-1120", "SEC-0304", "T-SYS-118-01", "FD-3-2"):
            assert is_valid_object_id(oid), oid

    def test_malformed_ids_rejected(self) -> None:
        for oid in ("obj-1120", "OBJ_1120", "OBJ", "OBJ-", "-1120", "OBJ--1", ""):
            assert not is_valid_object_id(oid), oid

    def test_overlong_id_rejected(self) -> None:
        assert not is_valid_object_id("OBJ-" + "1" * 80)

    def test_object_rejects_malformed_id(self) -> None:
        with pytest.raises(ValidationError, match="Invalid object id"):
            _object(object_id="obj-1120")

    def test_object_rejects_malformed_parent(self) -> None:
        with pytest.raises(ValidationError, match="Invalid object id"):
            _object(parent="not a parent")

    def test_object_cannot_be_its_own_parent(self) -> None:
        with pytest.raises(ValidationError, match="own parent"):
            _object(parent="OBJ-1120")


@pytest.mark.unit
class TestFigureExclusivity:
    """R-310-008 — a figure's authoritative content is its notation."""

    def test_figure_requires_notation(self) -> None:
        with pytest.raises(ValidationError, match="textual notation"):
            _object(type=ObjectType.FIGURE, body=None, notation=None)

    def test_figure_rejects_prose_body(self) -> None:
        with pytest.raises(ValidationError, match="SHALL NOT carry a prose body"):
            _object(
                type=ObjectType.FIGURE,
                body="a block diagram",
                notation="graph LR; A-->B",
            )

    def test_figure_with_notation_only_accepted(self) -> None:
        obj = _object(type=ObjectType.FIGURE, body=None, notation="graph LR; A-->B")
        assert obj.notation == "graph LR; A-->B"
        assert obj.body is None

    def test_prose_object_requires_body(self) -> None:
        with pytest.raises(ValidationError, match="non-empty body"):
            _object(body="   ")

    def test_prose_object_rejects_notation(self) -> None:
        with pytest.raises(ValidationError, match="SHALL NOT carry a notation"):
            _object(notation="graph LR; A-->B")

    @pytest.mark.parametrize(
        "otype",
        [ObjectType.HEADING, ObjectType.PARAGRAPH, ObjectType.LIST, ObjectType.TABLE],
    )
    def test_every_prose_type_accepts_a_body(self, otype: ObjectType) -> None:
        assert _object(type=otype).type is otype


@pytest.mark.unit
class TestDecisionTriggeredVersioning:
    """R-310-010 — a version exists only where a decision was taken."""

    def test_version_one_needs_no_record(self) -> None:
        # v1 is the object's first publication; the accept that lifted its
        # draft may not yet be attached by the constructing caller.
        assert _object(version=1, last_review=None).version == 1

    def test_version_beyond_one_requires_a_record(self) -> None:
        with pytest.raises(ValidationError, match="SHALL carry the review decision"):
            _object(version=2, last_review=None)

    def test_version_beyond_one_with_record_accepted(self) -> None:
        obj = _object(
            version=7,
            review_state=ReviewState.ACCEPTED,
            last_review=_accept_record(),
        )
        assert obj.version == 7
        assert obj.last_review is not None
        assert obj.last_review.actor == "o.mathieu"

    def test_rejection_cannot_produce_a_version(self) -> None:
        with pytest.raises(ValidationError, match="rejected draft SHALL NOT produce"):
            _object(
                version=2,
                last_review=ReviewRecord(
                    decision=ReviewDecision.REJECT, actor="o.mathieu", at=NOW
                ),
            )

    def test_confirm_unchanged_produces_a_version(self) -> None:
        obj = _object(
            version=3,
            review_state=ReviewState.ACCEPTED,
            last_review=_accept_record(
                decision=ReviewDecision.CONFIRM_UNCHANGED,
                justification="Ramp limit unchanged by the 250 ms timing edit.",
            ),
        )
        assert obj.version == 3

    @pytest.mark.parametrize(
        ("decision", "expected"),
        [
            (ReviewDecision.ACCEPT, True),
            (ReviewDecision.AUTO_ACCEPT, True),
            (ReviewDecision.CONFIRM_UNCHANGED, True),
            (ReviewDecision.REJECT, False),
        ],
    )
    def test_advances_version_mapping(
        self, decision: ReviewDecision, expected: bool
    ) -> None:
        assert advances_version(decision) is expected

    def test_auto_accept_maps_to_its_own_state(self) -> None:
        # R-310-007: auto-accepted is distinct from accepted, because a
        # coverage figure must be able to separate cluster-granted acceptance
        # from individual examination.
        assert state_for_decision(ReviewDecision.AUTO_ACCEPT) is (
            ReviewState.AUTO_ACCEPTED
        )
        assert state_for_decision(ReviewDecision.ACCEPT) is ReviewState.ACCEPTED
        # The distinction must survive serialisation: R-310-007 requires it in
        # storage and in every API response, not only in the Python enum.
        assert ReviewState.AUTO_ACCEPTED.value == "auto-accepted"
        assert ReviewState.ACCEPTED.value == "accepted"


@pytest.mark.unit
class TestReviewRecord:
    """R-310-150 / R-310-305 — dispositions are attributable evidence."""

    def test_confirm_unchanged_requires_justification(self) -> None:
        with pytest.raises(ValidationError, match="requires a justification"):
            ReviewRecord(
                decision=ReviewDecision.CONFIRM_UNCHANGED, actor="m.roche", at=NOW
            )

    def test_confirm_unchanged_rejects_blank_justification(self) -> None:
        with pytest.raises(ValidationError, match="requires a justification"):
            ReviewRecord(
                decision=ReviewDecision.CONFIRM_UNCHANGED,
                actor="m.roche",
                at=NOW,
                justification="   ",
            )

    def test_accept_does_not_require_justification(self) -> None:
        assert _accept_record().justification is None

    def test_blank_actor_rejected(self) -> None:
        with pytest.raises(ValidationError, match="actor SHALL NOT be blank"):
            ReviewRecord(decision=ReviewDecision.ACCEPT, actor="  ", at=NOW)


@pytest.mark.unit
class TestReviewRequest:
    def test_confirm_unchanged_requires_justification(self) -> None:
        with pytest.raises(ValidationError, match="requires a justification"):
            ObjectReviewRequest(
                decision=ReviewDecision.CONFIRM_UNCHANGED, expected_version=4
            )

    def test_expected_version_is_mandatory_and_positive(self) -> None:
        # R-310-192: the optimistic version check is the correctness
        # guarantee once a lease has lapsed, so the caller must state what it
        # believes it is modifying.
        with pytest.raises(ValidationError):
            ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=0)


@pytest.mark.unit
class TestWorkingDraft:
    """R-310-009 — a draft carries the negotiation, and creates no version."""

    def _draft(self, **overrides: object) -> WorkingDraft:
        payload: dict[str, object] = {
            "object_id": "OBJ-1120",
            "project_id": "adas-brake-ctrl",
            "container": "030-ARCHITECTURE-DESIGN",
            "type": ObjectType.PARAGRAPH,
            "body": "Ramp limited to 140 Nm/s.",
            "negotiation_id": "NEG-0031",
            "base_version": 12,
            "iteration": 2,
            "written_at": NOW,
            "written_by": "agent:architect",
        }
        payload.update(overrides)
        return WorkingDraft.model_validate(payload)

    def test_draft_accepted(self) -> None:
        draft = self._draft()
        assert draft.iteration == 2
        assert draft.base_version == 12

    def test_draft_has_no_version_field(self) -> None:
        # The absence is the point: a draft is not a version (R-310-009).
        assert "version" not in WorkingDraft.model_fields

    def test_draft_enforces_figure_exclusivity(self) -> None:
        with pytest.raises(ValidationError, match="textual notation"):
            self._draft(type=ObjectType.FIGURE, body=None, notation=None)

    def test_blank_negotiation_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="negotiation_id SHALL NOT be blank"):
            self._draft(negotiation_id="  ")

    def test_iteration_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._draft(iteration=0)


@pytest.mark.unit
class TestObjectLock:
    """R-310-190…193 — lease-based, agent-capable, visible."""

    def _lock(self, **overrides: object) -> ObjectLock:
        payload: dict[str, object] = {
            "object_id": "OBJ-1124",
            "holder": "agent:architect",
            "holder_kind": HolderKind.AGENT,
            "acquired_at": NOW,
            "expires_at": NOW + timedelta(minutes=15),
        }
        payload.update(overrides)
        return ObjectLock.model_validate(payload)

    def test_agent_is_a_valid_holder(self) -> None:
        # R-310-191: an agent redrafting while a reviewer edits produces the
        # same lost update as two humans, so it takes the same lock.
        assert self._lock().holder_kind is HolderKind.AGENT

    def test_human_is_a_valid_holder(self) -> None:
        lock = self._lock(holder="o.mathieu", holder_kind=HolderKind.HUMAN)
        assert lock.holder_kind is HolderKind.HUMAN

    def test_expiry_must_follow_acquisition(self) -> None:
        with pytest.raises(ValidationError, match="expiry SHALL be after"):
            self._lock(expires_at=NOW)

    def test_blank_holder_rejected(self) -> None:
        with pytest.raises(ValidationError, match="holder SHALL NOT be blank"):
            self._lock(holder=" ")

    def test_lease_expires(self) -> None:
        lock = self._lock()
        assert not lock.is_expired(NOW + timedelta(minutes=14))
        assert lock.is_expired(NOW + timedelta(minutes=15))
        assert lock.is_expired(NOW + timedelta(minutes=16))

    def test_held_by_requires_both_identity_and_freshness(self) -> None:
        lock = self._lock()
        assert lock.is_held_by("agent:architect", NOW + timedelta(minutes=1))
        assert not lock.is_held_by("o.mathieu", NOW + timedelta(minutes=1))
        assert not lock.is_held_by("agent:architect", NOW + timedelta(minutes=20))


@pytest.mark.unit
class TestPublicProjection:
    def test_from_object_carries_review_and_lock(self) -> None:
        obj = _object(
            version=12,
            review_state=ReviewState.STALE,
            last_review=_accept_record(),
            produced_by=ProducedBy(workflow="WF-002", workflow_version=3),
            cycle_version=4,
        )
        lock = ObjectLock(
            object_id="OBJ-1120",
            holder="agent:architect",
            holder_kind=HolderKind.AGENT,
            acquired_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
        )
        pub = DocObjectPublic.from_object(obj, has_working_draft=True, lock=lock)
        assert pub.object_id == "OBJ-1120"
        assert pub.version == 12
        assert pub.review_state is ReviewState.STALE
        assert pub.has_working_draft is True
        assert pub.lock is not None
        assert pub.lock.holder == "agent:architect"
        assert pub.produced_by is not None
        assert pub.produced_by.workflow == "WF-002"
        assert pub.cycle_version == 4

    def test_public_model_withholds_project_id(self) -> None:
        # project_id is a storage/routing concern; the API scopes by path.
        assert "project_id" not in DocObjectPublic.model_fields

    def test_public_model_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            DocObjectPublic.model_validate(
                {
                    "object_id": "OBJ-1120",
                    "container": "030",
                    "type": ObjectType.PARAGRAPH,
                    "ordinal": 1,
                    "version": 1,
                    "review_state": ReviewState.PROPOSED,
                    "created_at": NOW,
                    "created_by": "x",
                    "updated_at": NOW,
                    "updated_by": "x",
                    "surprise": True,
                }
            )

    def test_defaults_when_no_draft_and_no_lock(self) -> None:
        pub = DocObjectPublic.from_object(_object())
        assert pub.has_working_draft is False
        assert pub.lock is None
