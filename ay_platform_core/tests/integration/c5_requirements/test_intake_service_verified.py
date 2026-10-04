# =============================================================================
# File: test_intake_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_intake_service_verified.py
# Description: Integration tests for IntakeService against real MinIO, with
#              real supplied files.
#
#              The three assertions this tier exists for:
#                - a DEGRADED drop cannot be split until a human confirms the
#                  extracted text (R-310-061) — OCR shifts the very offsets
#                  the split check relies on;
#                - a split is impossible without a RECORDED atomicity finding
#                  (R-310-093), so a caller cannot manufacture its own
#                  justification in the request;
#                - splitting ALWAYS yields a rework request too (R-310-094),
#                  because the API returns the pair.
#
# @relation validates:R-310-060
# @relation validates:R-310-061
# @relation validates:R-310-062
# @relation validates:R-310-063
# @relation validates:R-310-092
# @relation validates:R-310-093
# @relation validates:R-310-094
# =============================================================================

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest

from ay_platform_core.c5_requirements.intake.intervals import TextInterval
from ay_platform_core.c5_requirements.intake.models import (
    ATOMICITY_CRITERION,
    SourceClass,
    SourceFormat,
)
from ay_platform_core.c5_requirements.intake.service import (
    IntakeRefusedError,
    IntakeService,
    derive_id_prefix,
)
from ay_platform_core.c5_requirements.intake.storage import IntakeStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

pytestmark = pytest.mark.integration

PID = "adas"
DROP = "CUST-2026-W14"
AGENT = "agent:req-analyst"
HUMAN = "o.mathieu"

_PROSE = "This chapter describes the braking subsystem."
_AGGLOMERATED = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms, and limit the torque ramp to 140 Nm/s."
)
_ATOMIC = "The diagnostic session shall time out after 5 s of inactivity."
_SPLIT_AT = _AGGLOMERATED.index(", and limit")

_DROP_TEXT = f"{_PROSE}\n{_AGGLOMERATED}\n{_ATOMIC}\n"

_REQIF = f"""<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">
  <CORE-CONTENT><REQ-IF-CONTENT><SPEC-OBJECTS>
    <SPEC-OBJECT IDENTIFIER="REQ-SYS-118">
      <VALUES><ATTRIBUTE-VALUE-STRING THE-VALUE="{_AGGLOMERATED}"/></VALUES>
    </SPEC-OBJECT>
  </SPEC-OBJECTS></REQ-IF-CONTENT></CORE-CONTENT>
</REQ-IF>""".encode()


@pytest.fixture
def service(c5_storage: RequirementsStorage) -> Iterator[IntakeService]:
    yield IntakeService(IntakeStorage(c5_storage))


async def _ingest_md(service: IntakeService, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "filename": "CUST-2026-W14.md",
        "payload": _DROP_TEXT.encode(),
        "source_format": SourceFormat.MD,
        "content_type": "text/markdown",
    }
    kwargs.update(overrides)
    return await service.ingest(PID, DROP, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Ingest — R-310-060 / R-310-062
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_stores_requirements_with_resolvable_anchors(
    service: IntakeService,
) -> None:
    report = await _ingest_md(service)
    assert report.count == 2  # type: ignore[attr-defined]
    # Every stored requirement is read back WITH anchor verification, which
    # is the default — so this passing means the anchors resolve.
    for requirement_id in report.requirement_ids:  # type: ignore[attr-defined]
        stored = await service._storage.get_requirement(PID, DROP, requirement_id)
        assert stored.text in _DROP_TEXT


@pytest.mark.asyncio
async def test_descriptive_prose_is_not_ingested(service: IntakeService) -> None:
    report = await _ingest_md(service)
    texts = [
        (await service._storage.get_requirement(PID, DROP, rid)).text
        for rid in report.requirement_ids  # type: ignore[attr-defined]
    ]
    assert _PROSE not in texts


@pytest.mark.asyncio
async def test_derived_ids_are_deterministic(service: IntakeService) -> None:
    # A re-ingest must be comparable, not duplicated.
    first = await _ingest_md(service)
    second = await _ingest_md(service)
    assert first.requirement_ids == second.requirement_ids  # type: ignore[attr-defined]
    assert first.requirement_ids[0].startswith(derive_id_prefix(DROP))  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_issuers_own_identifier_wins(service: IntakeService) -> None:
    """A contract review speaks in the customer's numbering, not ours."""
    report = await service.ingest(
        PID, "CUST-REQIF", filename="drop.reqif", payload=_REQIF,
        source_format=SourceFormat.REQIF,
    )
    assert report.requirement_ids == ("REQ-SYS-118",)


@pytest.mark.asyncio
async def test_a_textual_drop_needs_no_verification(service: IntakeService) -> None:
    report = await _ingest_md(service)
    assert report.needs_verification is False  # type: ignore[attr-defined]
    assert report.source_class is SourceClass.STRUCTURED  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The degraded gate — R-310-061
# ---------------------------------------------------------------------------


def _pdf_like_degraded() -> bytes:
    """A payload the PDF reader will refuse, so OCR is simulated via MD.

    The gate under test is the CLASS, not the parser, so the drop is ingested
    as Markdown and reclassified — authoring a scanned PDF would test pypdf.
    """
    return _DROP_TEXT.encode()


async def _ingest_degraded(service: IntakeService) -> tuple[str, ...]:
    report = await service.ingest(
        PID, DROP, filename="scan.md", payload=_pdf_like_degraded(),
        source_format=SourceFormat.MD,
    )
    ids = report.requirement_ids
    # Reclassify as the OCR path would, through the one permitted update.
    for requirement_id in ids:
        stored = await service._storage.get_requirement(PID, DROP, requirement_id)
        await service._storage.put_requirement(
            stored.model_copy(update={"source_class": SourceClass.DEGRADED}),
            allow_update=True,
        )
    return ids


@pytest.mark.asyncio
async def test_a_degraded_requirement_cannot_be_split(
    service: IntakeService,
) -> None:
    """R-310-061 — OCR shifts the offsets the split check relies on."""
    ids = await _ingest_degraded(service)
    target = ids[0]
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence.", actor=AGENT,
    )
    with pytest.raises(IntakeRefusedError, match="degraded source"):
        await service.propose_split(
            PID, DROP, target,
            spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
            actor=AGENT,
        )


@pytest.mark.asyncio
async def test_verification_clears_the_gate_and_is_reported(
    service: IntakeService,
) -> None:
    ids = await _ingest_degraded(service)
    cleared = await service.verify_extraction(PID, DROP, actor=HUMAN)
    assert set(cleared) == set(ids)

    stored = await service._storage.get_requirement(PID, DROP, ids[0])
    assert stored.verified_by == HUMAN
    assert stored.needs_verification is False


@pytest.mark.asyncio
async def test_verifying_twice_clears_nothing_the_second_time(
    service: IntakeService,
) -> None:
    await _ingest_degraded(service)
    await service.verify_extraction(PID, DROP, actor=HUMAN)
    assert await service.verify_extraction(PID, DROP, actor=HUMAN) == ()


# ---------------------------------------------------------------------------
# Splitting — R-310-093 / R-310-094
# ---------------------------------------------------------------------------


async def _agglomerated_id(service: IntakeService) -> str:
    report = await _ingest_md(service)
    for requirement_id in report.requirement_ids:  # type: ignore[attr-defined]
        stored = await service._storage.get_requirement(PID, DROP, requirement_id)
        if stored.text == _AGGLOMERATED:
            return str(requirement_id)
    raise AssertionError("the agglomerated requirement was not ingested")


@pytest.mark.asyncio
async def test_a_split_without_a_recorded_finding_is_refused(
    service: IntakeService,
) -> None:
    """R-310-093 — a caller cannot manufacture its own justification."""
    target = await _agglomerated_id(service)
    with pytest.raises(IntakeRefusedError, match="not a free action"):
        await service.propose_split(
            PID, DROP, target,
            spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
            actor=AGENT,
        )


@pytest.mark.asyncio
async def test_another_criterion_does_not_authorise_a_split(
    service: IntakeService,
) -> None:
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id="CRIT-QUA-004",
        detail="Contains the unbound term 'quickly' with no quantified bound.",
        actor=AGENT,
    )
    with pytest.raises(IntakeRefusedError, match="not a free action"):
        await service.propose_split(
            PID, DROP, target,
            spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
            actor=AGENT,
        )


@pytest.mark.asyncio
async def test_a_justified_split_yields_the_pair(service: IntakeService) -> None:
    """R-310-094 — the API returns both, so neither can be skipped."""
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence: timing and ramp.",
        actor=AGENT,
    )
    proposal, rework = await service.propose_split(
        PID, DROP, target,
        spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
        actor=AGENT,
    )

    assert proposal.fragment_ids == (f"{target}/1", f"{target}/2")
    assert rework.requirement_id == target
    assert rework.criterion_id == ATOMICITY_CRITERION
    assert "please issue them separately" in rework.detail


@pytest.mark.asyncio
async def test_the_rework_request_is_stored_and_listable(
    service: IntakeService,
) -> None:
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence: timing and ramp.",
        actor=AGENT,
    )
    await service.propose_split(
        PID, DROP, target,
        spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
        actor=AGENT,
    )
    owed = await service.rework_requests(PID, DROP)
    assert [r.requirement_id for r in owed] == [target]
    assert owed[0].anchor.location.startswith("line ")


@pytest.mark.asyncio
async def test_a_lossy_split_is_refused_by_the_proposal_itself(
    service: IntakeService,
) -> None:
    """R-310-092 reaching the service, with the lost text named."""
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence: timing and ramp.",
        actor=AGENT,
    )
    with pytest.raises(ValueError, match="does not tile the source"):
        await service.propose_split(
            PID, DROP, target,
            spans=(TextInterval(0, 20), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
            actor=AGENT,
        )


@pytest.mark.asyncio
async def test_statements_are_derived_when_not_supplied(
    service: IntakeService,
) -> None:
    # The interval carries the separator for the audit; the statement a
    # reviewer reads should not start on a comma.
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence: timing and ramp.",
        actor=AGENT,
    )
    proposal, _ = await service.propose_split(
        PID, DROP, target,
        spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
        actor=AGENT,
    )
    assert proposal.fragments[1].interval.slice_of(_AGGLOMERATED).startswith(",")
    assert proposal.fragments[1].statement.startswith("and limit the torque")


@pytest.mark.asyncio
async def test_fragments_are_readable_back_for_the_aggregation(
    service: IntakeService,
) -> None:
    # Feeds R-310-096: a split requirement's coverage is the aggregate of
    # these, never its own.
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence: timing and ramp.",
        actor=AGENT,
    )
    await service.propose_split(
        PID, DROP, target,
        spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
        actor=AGENT,
    )
    fragments = await service.fragments_of(PID, DROP, target)
    assert len(fragments) == 2
    assert "".join(f.interval.slice_of(_AGGLOMERATED) for f in fragments) == (
        _AGGLOMERATED
    )


@pytest.mark.asyncio
async def test_an_unsplit_requirement_has_no_fragments(
    service: IntakeService,
) -> None:
    target = await _agglomerated_id(service)
    assert await service.fragments_of(PID, DROP, target) == ()


# ---------------------------------------------------------------------------
# Findings — R-310-063
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_findings_round_trip_per_criterion(service: IntakeService) -> None:
    target = await _agglomerated_id(service)
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence.", actor=AGENT,
    )
    await service.record_finding(
        PID, DROP, requirement_id=target, criterion_id="CRIT-QUA-004",
        detail="Contains the unbound term 'quickly' with no quantified bound.",
        actor=AGENT,
    )
    findings = await service.findings_of(PID, DROP, target)
    assert [f.criterion_id for f in findings] == [
        ATOMICITY_CRITERION, "CRIT-QUA-004",
    ]


@pytest.mark.asyncio
async def test_a_later_verdict_replaces_the_earlier_one(
    service: IntakeService,
) -> None:
    # A criterion either fails or it does not; accumulating verdicts would
    # leave a reviewer asking which one is current.
    target = await _agglomerated_id(service)
    for detail in ("First assessment of the atomicity defect here.",
                   "Revised assessment of the atomicity defect here."):
        await service.record_finding(
            PID, DROP, requirement_id=target,
            criterion_id=ATOMICITY_CRITERION, detail=detail, actor=AGENT,
        )
    findings = await service.findings_of(PID, DROP, target)
    assert len(findings) == 1
    assert findings[0].detail.startswith("Revised")


@pytest.mark.asyncio
async def test_a_finding_about_an_unreceived_requirement_is_refused(
    service: IntakeService,
) -> None:
    # Noise, and the read also re-verifies the anchor it will be quoted
    # against.
    await _ingest_md(service)
    with pytest.raises(FileNotFoundError):
        await service.record_finding(
            PID, DROP, requirement_id="REQ-NEVER-SENT",
            criterion_id=ATOMICITY_CRITERION,
            detail="This requirement was never received at all.", actor=AGENT,
        )


# ---------------------------------------------------------------------------
# A real DOCX end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_docx_drop_ingests_and_splits(service: IntakeService) -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for text in (_PROSE, _AGGLOMERATED, _ATOMIC):
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)

    report = await service.ingest(
        PID, "CUST-DOCX", filename="drop.docx", payload=buffer.getvalue(),
        source_format=SourceFormat.DOCX,
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    assert report.count == 2
    assert report.source_class is SourceClass.TEXTUAL

    target = ""
    for rid in report.requirement_ids:
        stored = await service._storage.get_requirement(PID, "CUST-DOCX", rid)
        if stored.text == _AGGLOMERATED:
            target = rid
            break
    assert target, "the agglomerated requirement was not ingested"
    await service.record_finding(
        PID, "CUST-DOCX", requirement_id=target, criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence: timing and ramp.",
        actor=AGENT,
    )
    proposal, rework = await service.propose_split(
        PID, "CUST-DOCX", target,
        spans=(TextInterval(0, _SPLIT_AT), TextInterval(_SPLIT_AT, len(_AGGLOMERATED))),
        actor=AGENT,
    )
    assert len(proposal.fragments) == 2
    assert rework.anchor.source_file == "drop.docx"
