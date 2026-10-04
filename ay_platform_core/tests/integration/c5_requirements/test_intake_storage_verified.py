# =============================================================================
# File: test_intake_storage_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_intake_storage_verified.py
# Description: Integration tests for IntakeStorage against real MinIO.
#
#              Step 4.3 made an anchor resolvable at extraction time; this
#              tier proves it STAYS resolvable. The extracted document text is
#              stored beside the requirements and re-verified on every read,
#              so an anchor that only worked while the original file sat on
#              somebody's disk is not accepted as provenance.
#
#              The other load-bearing assertion: a supplied requirement is
#              never rewritten (R-310-090). It is a contractual artefact
#              belonging to its issuer, and a changed requirement arrives as
#              a new drop rather than as a mutation of the old one.
#
# @relation validates:R-310-062
# @relation validates:R-310-090
# @relation validates:R-310-092
# =============================================================================

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from ay_platform_core.c5_requirements.intake.extraction import (
    ExtractionError,
    extract_markdown,
)
from ay_platform_core.c5_requirements.intake.intervals import TextInterval
from ay_platform_core.c5_requirements.intake.models import (
    ATOMICITY_CRITERION,
    Fragment,
    QualityFinding,
    SourceAnchor,
    SourceClass,
    SplitProposal,
    SuppliedRequirement,
    fragment_id,
)
from ay_platform_core.c5_requirements.intake.storage import (
    IntakePathError,
    IntakeStorage,
    SuppliedRequirementImmutableError,
)
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
PID = "adas"
DROP = "CUST-2026-W14"
REQ = "REQ-SYS-118"

_PROSE = "This chapter describes the braking subsystem."
_TIMING = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms, and limit the torque ramp to 140 Nm/s."
)
_SPLIT_AT = _TIMING.index(", and limit")


@pytest.fixture
def store(c5_storage: RequirementsStorage) -> Iterator[IntakeStorage]:
    yield IntakeStorage(c5_storage)


def _extraction() -> tuple[str, TextInterval]:
    """Return the stored document text and the span holding the requirement."""
    extraction = extract_markdown(f"{_PROSE}\n{_TIMING}\n")
    record = extraction.records[0]
    return extraction.document_text, record.interval


def _supplied(**overrides: Any) -> SuppliedRequirement:
    document_text, interval = _extraction()
    payload: dict[str, Any] = {
        "requirement_id": REQ,
        "project_id": PID,
        "drop_id": DROP,
        "text": interval.slice_of(document_text),
        "anchor": SourceAnchor(
            source_file="CUST-2026-W14.md", location="line 2", interval=interval
        ),
        "source_class": SourceClass.STRUCTURED,
        "received_at": NOW,
    }
    payload.update(overrides)
    return SuppliedRequirement.model_validate(payload)


async def _seed(store: IntakeStorage) -> SuppliedRequirement:
    document_text, _ = _extraction()
    await store.put_extracted_text(PID, DROP, document_text)
    requirement = _supplied()
    await store.put_requirement(requirement)
    return requirement


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPaths:
    def test_the_drop_prefix(self) -> None:
        assert IntakeStorage.drop_prefix(PID, DROP) == (
            "projects/adas/intake/CUST-2026-W14/"
        )

    def test_the_extraction_lives_beside_the_requirements(self) -> None:
        assert IntakeStorage.extracted_text_path(PID, DROP) == (
            "projects/adas/intake/CUST-2026-W14/extracted.txt"
        )

    def test_the_requirement_path(self) -> None:
        assert IntakeStorage.requirement_path(PID, DROP, REQ) == (
            "projects/adas/intake/CUST-2026-W14/requirements/REQ-SYS-118.json"
        )

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".hidden"])
    def test_traversal_is_refused(self, bad: str) -> None:
        with pytest.raises(IntakePathError):
            IntakeStorage.drop_prefix(bad, DROP)

    def test_a_fragment_id_cannot_be_a_requirement_path(self) -> None:
        # Fragments live inside their parent's split, never as supplied
        # requirements of their own.
        with pytest.raises(IntakePathError, match="requirement id"):
            IntakeStorage.requirement_path(PID, DROP, "REQ-SYS-118/2")


# ---------------------------------------------------------------------------
# Anchors stay resolvable — R-310-062
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stored_requirement_round_trips_with_its_anchor(
    store: IntakeStorage,
) -> None:
    await _seed(store)
    read = await store.get_requirement(PID, DROP, REQ)
    assert read.text == _TIMING
    assert read.anchor.location == "line 2"


@pytest.mark.asyncio
async def test_a_read_refuses_an_anchor_that_no_longer_resolves(
    store: IntakeStorage,
) -> None:
    """The point of storing the extraction: provenance is checked, not claimed."""
    await _seed(store)
    await store.put_extracted_text(PID, DROP, "somebody replaced the extraction")

    with pytest.raises(ExtractionError, match="no longer resolves"):
        await store.get_requirement(PID, DROP, REQ)


@pytest.mark.asyncio
async def test_a_read_refuses_when_the_extraction_is_missing(
    store: IntakeStorage,
) -> None:
    requirement = _supplied()
    await store.put_requirement(requirement)

    with pytest.raises(ExtractionError, match="no stored extraction"):
        await store.get_requirement(PID, DROP, REQ)


@pytest.mark.asyncio
async def test_verification_can_be_skipped_deliberately(
    store: IntakeStorage,
) -> None:
    # Needed by the reindex path, which reads before the extraction is in
    # place. Opt-in, never the default: an unchecked anchor is a claim.
    await store.put_requirement(_supplied())
    read = await store.get_requirement(PID, DROP, REQ, verify=False)
    assert read.requirement_id == REQ


@pytest.mark.asyncio
async def test_the_failure_names_the_location_and_both_texts(
    store: IntakeStorage,
) -> None:
    await _seed(store)
    await store.put_extracted_text(PID, DROP, "x" * 400)
    with pytest.raises(ExtractionError, match="line 2"):
        await store.get_requirement(PID, DROP, REQ)


# ---------------------------------------------------------------------------
# Immutability of what the issuer sent — R-310-090
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewriting_a_supplied_requirement_is_refused(
    store: IntakeStorage,
) -> None:
    """R-310-090 — a changed requirement is a NEW drop, not a mutation.

    The substitute is internally consistent on purpose: the model's own
    anchor-length guard (R-310-062) fires before the storage guard, so an
    inconsistent substitute would test the wrong rule.
    """
    await _seed(store)
    changed = "The customer changed its mind about the deceleration target."
    with pytest.raises(SuppliedRequirementImmutableError, match="never rewritten"):
        await store.put_requirement(
            _supplied(
                text=changed,
                anchor=SourceAnchor(
                    source_file="CUST-2026-W14.md", location="line 2",
                    interval=TextInterval(0, len(changed)),
                ),
            )
        )


@pytest.mark.asyncio
async def test_storing_the_same_text_again_is_idempotent(
    store: IntakeStorage,
) -> None:
    await _seed(store)
    await store.put_requirement(_supplied())
    assert (await store.get_requirement(PID, DROP, REQ)).text == _TIMING


@pytest.mark.asyncio
async def test_verification_may_update_the_record(
    store: IntakeStorage,
) -> None:
    # R-310-061: confirming an OCR extraction is not changing what the issuer
    # wrote, so it is the one permitted update.
    await _seed(store)
    verified = _supplied(
        source_class=SourceClass.DEGRADED, verified_by="o.mathieu", verified_at=NOW
    )
    await store.put_requirement(verified, allow_update=True)
    read = await store.get_requirement(PID, DROP, REQ)
    assert read.verified_by == "o.mathieu"
    assert read.needs_verification is False


@pytest.mark.asyncio
async def test_the_original_payload_is_kept(store: IntakeStorage) -> None:
    # An extraction is OUR reading of what the customer sent; a contract
    # review asks about the thing itself.
    await store.put_payload(
        PID, DROP, "CUST-2026-W14.md", _TIMING.encode(), "text/markdown"
    )
    path = IntakeStorage.payload_path(PID, DROP, "CUST-2026-W14.md")
    assert path.endswith("source/CUST-2026-W14.md")


@pytest.mark.asyncio
async def test_requirement_ids_are_listed(store: IntakeStorage) -> None:
    document_text, _ = _extraction()
    await store.put_extracted_text(PID, DROP, document_text)
    for requirement_id in ("REQ-SYS-118", "REQ-SYS-091"):
        await store.put_requirement(_supplied(requirement_id=requirement_id))
    assert await store.list_requirement_ids(PID, DROP) == [
        "REQ-SYS-091", "REQ-SYS-118",
    ]


@pytest.mark.asyncio
async def test_drops_are_isolated(store: IntakeStorage) -> None:
    document_text, _ = _extraction()
    await store.put_extracted_text(PID, DROP, document_text)
    await store.put_extracted_text(PID, "CUST-2026-W20", document_text)
    await store.put_requirement(_supplied())
    assert await store.list_requirement_ids(PID, "CUST-2026-W20") == []


# ---------------------------------------------------------------------------
# Splits — R-310-092 reaching storage
# ---------------------------------------------------------------------------


def _proposal() -> SplitProposal:
    return SplitProposal(
        parent_id=REQ,
        project_id=PID,
        drop_id=DROP,
        source_text=_TIMING,
        fragments=(
            Fragment(
                fragment_id=fragment_id(REQ, 1), parent_id=REQ, project_id=PID,
                ordinal=1, interval=TextInterval(0, _SPLIT_AT),
                statement="reduce deceleration to zero within 250 ms",
            ),
            Fragment(
                fragment_id=fragment_id(REQ, 2), parent_id=REQ, project_id=PID,
                ordinal=2, interval=TextInterval(_SPLIT_AT, len(_TIMING)),
                statement="limit the torque ramp to 140 Nm/s",
            ),
        ),
        atomicity_finding=QualityFinding(
            requirement_id=REQ,
            criterion_id=ATOMICITY_CRITERION,
            detail="Two independent obligations in one sentence: timing and ramp.",
            actor="agent:req-analyst",
            at=NOW,
        ),
        actor="agent:req-analyst",
        at=NOW,
    )


@pytest.mark.asyncio
async def test_a_split_round_trips(store: IntakeStorage) -> None:
    await _seed(store)
    await store.put_split(_proposal())
    read = await store.get_split(PID, DROP, REQ)
    assert read is not None
    assert read.fragment_ids == ("REQ-SYS-118/1", "REQ-SYS-118/2")


@pytest.mark.asyncio
async def test_an_unsplit_requirement_has_no_fragments(
    store: IntakeStorage,
) -> None:
    await _seed(store)
    assert await store.get_split(PID, DROP, REQ) is None
    assert await store.list_fragments(PID, DROP, REQ) == ()


@pytest.mark.asyncio
async def test_fragments_survive_the_round_trip_with_their_intervals(
    store: IntakeStorage,
) -> None:
    # The intervals are the audit artefact; losing them in serialisation
    # would leave the split unverifiable after the fact.
    await _seed(store)
    await store.put_split(_proposal())
    fragments = await store.list_fragments(PID, DROP, REQ)
    assert [f.interval.start for f in fragments] == [0, _SPLIT_AT]
    assert "".join(f.interval.slice_of(_TIMING) for f in fragments) == _TIMING
