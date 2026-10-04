# =============================================================================
# File: test_intake_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_intake_models.py
# Description: Unit tests for the intake and splitting contracts of
#              310-SPEC §4.4 / §4.5.
#
#              The five load-bearing assertions:
#                - an unresolvable anchor is not an anchor (R-310-062);
#                - a finding citing no declared criterion cannot exist
#                  (R-310-063);
#                - a fragment id is DERIVED, so the issuer's namespace cannot
#                  be imitated (R-310-095);
#                - a split needs a failed atomicity finding to justify it
#                  (R-310-093) and must tile the source (R-310-092);
#                - a degraded source reports that it needs verification
#                  before anything is done to it (R-310-061).
#
# @relation validates:R-310-060
# @relation validates:R-310-061
# @relation validates:R-310-062
# @relation validates:R-310-063
# @relation validates:R-310-090
# @relation validates:R-310-093
# @relation validates:R-310-094
# @relation validates:R-310-095
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.intake.intervals import TextInterval
from ay_platform_core.c5_requirements.intake.models import (
    ATOMICITY_CRITERION,
    Fragment,
    QualityFinding,
    ReworkRequest,
    SourceAnchor,
    SourceClass,
    SourceFormat,
    SplitProposal,
    SuppliedRequirement,
    default_source_class,
    fragment_id,
)

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
PID = "adas"
REQ = "REQ-SYS-118"
DROP = "CUST-2026-W14"

SOURCE = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms, and limit the torque ramp to 140 Nm/s."
)
_SPLIT_AT = SOURCE.index(", and limit")


def _anchor(**overrides: Any) -> SourceAnchor:
    payload: dict[str, Any] = {
        "source_file": "CUST-2026-W14.docx",
        "location": "p.42 §7.3.1",
        "interval": TextInterval(0, len(SOURCE)),
    }
    payload.update(overrides)
    return SourceAnchor.model_validate(payload)


def _supplied(**overrides: Any) -> SuppliedRequirement:
    payload: dict[str, Any] = {
        "requirement_id": REQ,
        "project_id": PID,
        "drop_id": DROP,
        "text": SOURCE,
        "anchor": _anchor(),
        "source_class": SourceClass.TEXTUAL,
        "received_at": NOW,
    }
    payload.update(overrides)
    return SuppliedRequirement.model_validate(payload)


def _atomicity_finding(**overrides: Any) -> QualityFinding:
    payload: dict[str, Any] = {
        "requirement_id": REQ,
        "criterion_id": ATOMICITY_CRITERION,
        "detail": "Two independent obligations in one sentence: timing and ramp.",
        "actor": "agent:req-analyst",
        "at": NOW,
    }
    payload.update(overrides)
    return QualityFinding.model_validate(payload)


def _fragments() -> tuple[Fragment, ...]:
    return (
        Fragment(
            fragment_id=fragment_id(REQ, 1), parent_id=REQ, project_id=PID,
            ordinal=1, interval=TextInterval(0, _SPLIT_AT),
            statement="reduce deceleration to zero within 250 ms",
        ),
        Fragment(
            fragment_id=fragment_id(REQ, 2), parent_id=REQ, project_id=PID,
            ordinal=2, interval=TextInterval(_SPLIT_AT, len(SOURCE)),
            statement="limit the torque ramp to 140 Nm/s",
        ),
    )


def _proposal(**overrides: Any) -> SplitProposal:
    payload: dict[str, Any] = {
        "parent_id": REQ,
        "project_id": PID,
        "drop_id": DROP,
        "source_text": SOURCE,
        "fragments": _fragments(),
        "atomicity_finding": _atomicity_finding(),
        "actor": "agent:req-analyst",
        "at": NOW,
    }
    payload.update(overrides)
    return SplitProposal.model_validate(payload)


# ---------------------------------------------------------------------------
# Source classes — R-310-060 / R-310-061
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSourceClasses:
    def test_the_format_to_class_mapping(self) -> None:
        assert default_source_class(SourceFormat.REQIF) is SourceClass.STRUCTURED
        assert default_source_class(SourceFormat.XLSX) is SourceClass.STRUCTURED
        assert default_source_class(SourceFormat.MD) is SourceClass.STRUCTURED
        assert default_source_class(SourceFormat.DOCX) is SourceClass.TEXTUAL
        assert default_source_class(SourceFormat.PDF) is SourceClass.TEXTUAL

    def test_ocr_degrades_whatever_the_format_said(self) -> None:
        # The only class that depends on the FILE rather than the extension.
        assert default_source_class(
            SourceFormat.PDF, needed_ocr=True
        ) is SourceClass.DEGRADED

    def test_every_format_has_a_class(self) -> None:
        # A format with no class would silently get whichever default the
        # code happened to pick, which is how a degraded source slips through.
        for fmt in SourceFormat:
            assert isinstance(default_source_class(fmt), SourceClass)

    def test_a_degraded_source_needs_verification(self) -> None:
        """R-310-061 — OCR shifts character offsets, so R-310-092 would pass
        against corrupted text and lose a clause invisibly."""
        degraded = _supplied(source_class=SourceClass.DEGRADED)
        assert degraded.needs_verification is True

    def test_verification_clears_the_gate(self) -> None:
        verified = _supplied(
            source_class=SourceClass.DEGRADED, verified_by="o.mathieu",
            verified_at=NOW,
        )
        assert verified.needs_verification is False

    def test_a_textual_source_never_needs_verification(self) -> None:
        assert _supplied().needs_verification is False


# ---------------------------------------------------------------------------
# Anchors — R-310-062
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnchors:
    def test_a_valid_anchor(self) -> None:
        anchor = _anchor()
        assert anchor.source_file.endswith(".docx")
        assert anchor.location == "p.42 §7.3.1"

    def test_a_blank_file_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unresolvable anchor is no anchor"):
            _anchor(source_file="  ")

    def test_a_blank_location_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unresolvable anchor is no anchor"):
            _anchor(location="")

    def test_an_anchor_longer_than_its_text_is_refused(self) -> None:
        # It could not be resolved against the source, so it is not an anchor.
        with pytest.raises(ValidationError, match="anchor interval spans"):
            _supplied(text="short", anchor=_anchor(interval=TextInterval(0, 500)))


# ---------------------------------------------------------------------------
# Supplied requirements — R-310-090 / R-310-095
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuppliedRequirement:
    def test_a_supplied_requirement_is_never_a_fragment(self) -> None:
        # The namespaces must not be confusable in either direction.
        with pytest.raises(ValidationError, match="fragment namespace"):
            _supplied(requirement_id="REQ-SYS-118/2")

    def test_blank_text_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="SHALL carry its text"):
            _supplied(text="   ")

    def test_a_malformed_drop_id_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Invalid drop id"):
            _supplied(drop_id="../escape")

    def test_criticality_is_carried_through(self) -> None:
        assert _supplied(criticality="ASIL-D").criticality == "ASIL-D"


# ---------------------------------------------------------------------------
# Quality findings — R-310-063
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQualityFindings:
    def test_a_finding_must_cite_a_declared_criterion(self) -> None:
        """R-310-063 — an LLM's supply of plausible objections is unlimited."""
        with pytest.raises(ValidationError, match="SHALL cite a declared"):
            _atomicity_finding(criterion_id="QUALITY-1")

    def test_a_finding_must_say_what_is_wrong(self) -> None:
        with pytest.raises(ValidationError, match="SHALL say what is wrong"):
            _atomicity_finding(detail="bad")

    def test_a_valid_finding(self) -> None:
        finding = _atomicity_finding()
        assert finding.criterion_id == ATOMICITY_CRITERION


# ---------------------------------------------------------------------------
# Fragment identity — R-310-095
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFragmentIdentity:
    def test_the_id_is_derived(self) -> None:
        assert fragment_id(REQ, 2) == "REQ-SYS-118/2"

    def test_a_fragment_of_a_fragment_is_refused(self) -> None:
        with pytest.raises(ValueError, match="already a fragment"):
            fragment_id("REQ-SYS-118/1", 1)

    def test_a_zero_ordinal_is_refused(self) -> None:
        with pytest.raises(ValueError, match="SHALL be >= 1"):
            fragment_id(REQ, 0)

    def test_a_supplied_id_that_is_not_derived_is_refused(self) -> None:
        """The namespace cannot be made to resemble the issuer's own."""
        with pytest.raises(ValidationError, match="not derived from its parent"):
            Fragment(
                fragment_id="REQ-SYS-119", parent_id=REQ, project_id=PID,
                ordinal=1, interval=TextInterval(0, 10), statement="x",
            )

    def test_a_mismatched_ordinal_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not derived from its parent"):
            Fragment(
                fragment_id="REQ-SYS-118/1", parent_id=REQ, project_id=PID,
                ordinal=2, interval=TextInterval(0, 10), statement="x",
            )

    def test_export_exclusion_cannot_be_turned_off(self) -> None:
        # R-310-095: a property, not a stored flag, so nothing can set it.
        fragment = _fragments()[0]
        assert fragment.excluded_from_issuer_export is True
        assert "excluded_from_issuer_export" not in Fragment.model_fields


# ---------------------------------------------------------------------------
# Split proposals — R-310-092 / R-310-093
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitProposal:
    def test_a_justified_exhaustive_split_is_accepted(self) -> None:
        proposal = _proposal()
        assert proposal.fragment_ids == ("REQ-SYS-118/1", "REQ-SYS-118/2")

    def test_a_split_without_a_failed_atomicity_check_is_refused(self) -> None:
        """R-310-093 — otherwise splitting becomes the default reflex."""
        with pytest.raises(ValidationError, match="justified by a failed atomicity"):
            _proposal(
                atomicity_finding=_atomicity_finding(criterion_id="CRIT-QUA-004")
            )

    def test_the_finding_must_concern_the_requirement_being_split(self) -> None:
        with pytest.raises(ValidationError, match="SHALL concern the requirement"):
            _proposal(
                atomicity_finding=_atomicity_finding(requirement_id="REQ-SYS-999")
            )

    def test_a_single_fragment_split_is_refused(self) -> None:
        # One fragment is the requirement itself, renamed.
        with pytest.raises(ValidationError, match="at least two fragments"):
            _proposal(fragments=(_fragments()[0],))

    def test_a_lossy_split_is_refused_with_the_lost_text(self) -> None:
        """R-310-092 reaching the model boundary, quoting what was dropped."""
        head = Fragment(
            fragment_id=fragment_id(REQ, 1), parent_id=REQ, project_id=PID,
            ordinal=1, interval=TextInterval(0, 20), statement="partial",
        )
        tail = Fragment(
            fragment_id=fragment_id(REQ, 2), parent_id=REQ, project_id=PID,
            ordinal=2, interval=TextInterval(_SPLIT_AT, len(SOURCE)),
            statement="limit the torque ramp",
        )
        with pytest.raises(ValidationError, match="does not tile the source"):
            _proposal(fragments=(head, tail))

    def test_an_overlapping_split_is_refused(self) -> None:
        first = Fragment(
            fragment_id=fragment_id(REQ, 1), parent_id=REQ, project_id=PID,
            ordinal=1, interval=TextInterval(0, len(SOURCE)), statement="all",
        )
        second = Fragment(
            fragment_id=fragment_id(REQ, 2), parent_id=REQ, project_id=PID,
            ordinal=2, interval=TextInterval(10, 40), statement="middle",
        )
        with pytest.raises(ValidationError, match="does not tile the source"):
            _proposal(fragments=(first, second))

    def test_a_foreign_fragment_is_refused(self) -> None:
        stranger = Fragment(
            fragment_id="REQ-SYS-999/2", parent_id="REQ-SYS-999", project_id=PID,
            ordinal=2, interval=TextInterval(_SPLIT_AT, len(SOURCE)),
            statement="elsewhere",
        )
        with pytest.raises(ValidationError, match="does not belong to"):
            _proposal(fragments=(_fragments()[0], stranger))

    def test_the_parent_text_is_carried_not_copied_into_fragments(self) -> None:
        # R-310-090: the parent is the source of truth; a fragment holds an
        # interval and a readable statement, never a second copy.
        proposal = _proposal()
        assert proposal.source_text == SOURCE
        assert all(f.statement != SOURCE for f in proposal.fragments)


# ---------------------------------------------------------------------------
# Rework requests — R-310-094
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReworkRequest:
    def test_a_rework_request_cites_a_criterion_and_an_anchor(self) -> None:
        """R-310-094 — splitting internally without telling the issuer
        absorbs their debt silently."""
        request = ReworkRequest(
            requirement_id=REQ, project_id=PID, drop_id=DROP,
            criterion_id=ATOMICITY_CRITERION,
            detail="Two obligations in one sentence; please split at the source.",
            anchor=_anchor(), raised_by="o.mathieu", raised_at=NOW,
        )
        assert request.criterion_id == ATOMICITY_CRITERION
        assert request.anchor.location == "p.42 §7.3.1"

    def test_an_uncited_rework_request_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Invalid criterion id"):
            ReworkRequest(
                requirement_id=REQ, project_id=PID, drop_id=DROP,
                criterion_id="please-fix",
                detail="Two obligations in one sentence.",
                anchor=_anchor(), raised_by="o.mathieu", raised_at=NOW,
            )
