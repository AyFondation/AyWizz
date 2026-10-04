# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/service.py
# Description: The intake flow — 310-SPEC §4.4 / §4.5.
#
#              THREE RULES THIS MODULE ENFORCES, and how:
#
#              R-310-061  A DEGRADED source cannot be split until its text is
#                         human-verified. Interval anchors are character
#                         offsets and OCR shifts them, so R-310-092 would
#                         pass against corrupted text and lose a clause
#                         invisibly. The gate is here because it must hold
#                         however the split was requested.
#
#              R-310-093  A split needs a failed atomicity finding. Enforced
#                         by the model; the service additionally refuses a
#                         finding that was never recorded, so a caller cannot
#                         manufacture its own justification in the request.
#
#              R-310-094  A split SHALL also raise a rework request to the
#                         issuer. `propose_split` returns the PAIR, so a
#                         caller cannot obtain one without the other —
#                         correcting a supplied defect only internally
#                         absorbs the issuer's debt silently and leaves
#                         nothing to show at a contract review.
#
# @relation implements:R-310-060
# @relation implements:R-310-061
# @relation implements:R-310-062
# @relation implements:R-310-063
# @relation implements:R-310-092
# @relation implements:R-310-093
# @relation implements:R-310-094
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from .extraction import Extraction, extract
from .intervals import TextInterval, statement_of
from .models import (
    ATOMICITY_CRITERION,
    Fragment,
    QualityFinding,
    ReworkRequest,
    SourceAnchor,
    SourceClass,
    SourceFormat,
    SplitProposal,
    SuppliedRequirement,
    fragment_id,
)
from .storage import IntakeStorage

_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


class IntakeRefusedError(RuntimeError):
    """Raised when intake or a split cannot proceed as requested."""


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one ingested drop produced."""

    drop_id: str
    source_format: SourceFormat
    source_class: SourceClass
    requirement_ids: tuple[str, ...]
    needs_verification: bool
    """True when nothing downstream may proceed until a human confirms the
    extracted text (R-310-061)."""

    @property
    def count(self) -> int:
        """How many requirements the drop yielded."""
        return len(self.requirement_ids)


def derive_id_prefix(drop_id: str) -> str:
    """Return a requirement-id prefix derived from a drop identifier.

    Used only when the source supplies no identifier of its own. Derived
    rather than random so the same drop re-ingested yields the same ids,
    which is what lets a re-run be compared instead of duplicated.

    Args:
        drop_id: The drop identifier.

    Returns:
        An uppercase prefix such as `CUST-2026-W14`.
    """
    cleaned = _NON_ALNUM.sub("-", drop_id.upper()).strip("-")
    return cleaned or "REQ"


class IntakeService:
    """Ingest supplied drops, review them, and split what is not atomic.

    Args:
        storage: Source-of-truth persistence for drops.
    """

    def __init__(self, storage: IntakeStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Ingest — R-310-060 / R-310-062
    # ------------------------------------------------------------------

    async def ingest(
        self,
        project_id: str,
        drop_id: str,
        *,
        filename: str,
        payload: bytes,
        source_format: SourceFormat,
        content_type: str = "application/octet-stream",
        needed_ocr: bool = False,
        id_prefix: str | None = None,
        now: datetime | None = None,
    ) -> IngestReport:
        """Read a supplied drop and store what it contains.

        The original payload, the extracted document text and one record per
        requirement are all stored: the first because a contract review asks
        what the customer sent, the second because every anchor indexes into
        it, the third because that is the corpus.

        Raises:
            ExtractionError: When the payload cannot be read as that format.
        """
        moment = now or datetime.now(UTC)
        extraction = extract(payload, source_format, needed_ocr=needed_ocr)

        await self._storage.put_payload(
            project_id, drop_id, filename, payload, content_type
        )
        await self._storage.put_extracted_text(
            project_id, drop_id, extraction.document_text
        )

        prefix = id_prefix or derive_id_prefix(drop_id)
        ids: list[str] = []
        for ordinal, record in enumerate(extraction.records, start=1):
            requirement_id = _mint_id(record.native_id, prefix, ordinal)
            await self._storage.put_requirement(
                SuppliedRequirement(
                    requirement_id=requirement_id,
                    project_id=project_id,
                    drop_id=drop_id,
                    text=record.text,
                    anchor=SourceAnchor(
                        source_file=filename,
                        location=record.location,
                        interval=record.interval,
                    ),
                    source_class=extraction.source_class,
                    received_at=moment,
                )
            )
            ids.append(requirement_id)

        return _report(drop_id, extraction, tuple(ids))

    # ------------------------------------------------------------------
    # Verification of a degraded source — R-310-061
    # ------------------------------------------------------------------

    async def verify_extraction(
        self,
        project_id: str,
        drop_id: str,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Record that a human confirmed a degraded drop's extracted text.

        Confirming our extraction is not changing what the issuer wrote,
        which is why it is the one permitted update to a supplied
        requirement.

        Returns:
            The identifiers whose gate this cleared.
        """
        moment = now or datetime.now(UTC)
        cleared: list[str] = []
        for requirement_id in await self._storage.list_requirement_ids(
            project_id, drop_id
        ):
            requirement = await self._storage.get_requirement(
                project_id, drop_id, requirement_id
            )
            if not requirement.needs_verification:
                continue
            await self._storage.put_requirement(
                requirement.model_copy(
                    update={"verified_by": actor, "verified_at": moment}
                ),
                allow_update=True,
            )
            cleared.append(requirement_id)
        return tuple(cleared)

    # ------------------------------------------------------------------
    # Quality review — R-310-063
    # ------------------------------------------------------------------

    async def record_finding(
        self,
        project_id: str,
        drop_id: str,
        *,
        requirement_id: str,
        criterion_id: str,
        detail: str,
        actor: str,
        now: datetime | None = None,
    ) -> QualityFinding:
        """Record one quality finding against a supplied requirement.

        Reading the requirement first is deliberate: a finding about
        something that was never received is noise, and the read also
        re-verifies the anchor it will be quoted against.
        """
        await self._storage.get_requirement(project_id, drop_id, requirement_id)
        finding = QualityFinding(
            requirement_id=requirement_id,
            criterion_id=criterion_id,
            detail=detail,
            actor=actor,
            at=now or datetime.now(UTC),
        )
        await self._storage.put_finding(project_id, drop_id, finding)
        return finding

    async def findings_of(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> tuple[QualityFinding, ...]:
        """Return the findings recorded against one requirement."""
        return await self._storage.list_findings(project_id, drop_id, requirement_id)

    # ------------------------------------------------------------------
    # Splitting — R-310-061 / R-310-093 / R-310-094
    # ------------------------------------------------------------------

    async def propose_split(
        self,
        project_id: str,
        drop_id: str,
        requirement_id: str,
        *,
        spans: tuple[TextInterval, ...],
        actor: str,
        statements: tuple[str, ...] | None = None,
        now: datetime | None = None,
    ) -> tuple[SplitProposal, ReworkRequest]:
        """Split a supplied requirement, and tell the issuer about the defect.

        Returns BOTH the proposal and the rework request on purpose: a caller
        cannot obtain the split without the message to the issuer
        (R-310-094). Correcting a supplied defect only internally absorbs
        the issuer's debt silently and leaves nothing to show at a contract
        review.

        Args:
            project_id: Owning project.
            drop_id: The drop the requirement arrived in.
            requirement_id: The requirement to split.
            spans: The fragment intervals, which must tile the requirement's
                text (R-310-092).
            actor: Who proposed the split.
            statements: Readable statements per fragment; derived from the
                spans when omitted.
            now: Reference instant.

        Raises:
            IntakeRefusedError: When the source is degraded and unverified
                (R-310-061), or when no failed atomicity finding was ever
                recorded for this requirement (R-310-093).
        """
        moment = now or datetime.now(UTC)
        requirement = await self._storage.get_requirement(
            project_id, drop_id, requirement_id
        )

        if requirement.needs_verification:
            raise IntakeRefusedError(
                f"{requirement_id!r} comes from a degraded source whose text "
                "nobody has confirmed; splitting it would anchor fragments on "
                "character offsets that OCR may have shifted (R-310-061)"
            )

        findings = await self._storage.list_findings(
            project_id, drop_id, requirement_id
        )
        atomicity = next(
            (f for f in findings if f.criterion_id == ATOMICITY_CRITERION), None
        )
        if atomicity is None:
            raise IntakeRefusedError(
                f"no recorded {ATOMICITY_CRITERION} finding for "
                f"{requirement_id!r}; a split is a consequence of a failed "
                "atomicity check, not a free action (R-310-093)"
            )

        ordered = tuple(sorted(spans))
        fragments = tuple(
            Fragment(
                fragment_id=fragment_id(requirement_id, ordinal),
                parent_id=requirement_id,
                project_id=project_id,
                ordinal=ordinal,
                interval=span,
                statement=(
                    statements[ordinal - 1]
                    if statements and len(statements) >= ordinal
                    else statement_of(span, requirement.text)
                ),
            )
            for ordinal, span in enumerate(ordered, start=1)
        )

        # The proposal's own validator enforces R-310-092 and R-310-093; a
        # split that loses text cannot be constructed, let alone stored.
        proposal = SplitProposal(
            parent_id=requirement_id,
            project_id=project_id,
            drop_id=drop_id,
            source_text=requirement.text,
            fragments=fragments,
            atomicity_finding=atomicity,
            actor=actor,
            at=moment,
        )
        rework = ReworkRequest(
            requirement_id=requirement_id,
            project_id=project_id,
            drop_id=drop_id,
            criterion_id=ATOMICITY_CRITERION,
            detail=(
                f"{atomicity.detail} Split internally into "
                f"{len(fragments)} fragments; please issue them separately."
            ),
            anchor=requirement.anchor,
            raised_by=actor,
            raised_at=moment,
        )

        await self._storage.put_split(proposal)
        await self._storage.put_rework(rework)
        return proposal, rework

    async def fragments_of(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> tuple[Fragment, ...]:
        """Return a requirement's fragments, empty when it was not split.

        Feeds the aggregation of R-310-096: a split requirement's coverage is
        the aggregate of these, never its own.
        """
        return await self._storage.list_fragments(project_id, drop_id, requirement_id)

    async def split_of(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> SplitProposal | None:
        """Return a requirement's split proposal, or None when unsplit.

        Read by the change-absorption closure gate, which re-runs the
        exhaustiveness check of `R-310-092` against modified source text
        (`R-310-147`) and needs the ORIGINAL fragment intervals to do it.
        """
        return await self._storage.get_split(project_id, drop_id, requirement_id)

    async def rework_requests(
        self, project_id: str, drop_id: str
    ) -> tuple[ReworkRequest, ...]:
        """Return everything this drop owes its issuer (R-310-094)."""
        return await self._storage.list_rework(project_id, drop_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint_id(native_id: str | None, prefix: str, ordinal: int) -> str:
    """Return the identifier to store a record under.

    The issuer's own identifier wins whenever it is usable: a contract
    review speaks in the customer's numbering, not ours. A derived id is the
    fallback, and it is deterministic so a re-ingest can be compared rather
    than duplicated.
    """
    if native_id and _ID_RE.match(native_id):
        return native_id
    return f"{prefix}-{ordinal:03d}"


def _report(
    drop_id: str, extraction: Extraction, ids: tuple[str, ...]
) -> IngestReport:
    return IngestReport(
        drop_id=drop_id,
        source_format=extraction.source_format,
        source_class=extraction.source_class,
        requirement_ids=ids,
        needs_verification=extraction.source_class is SourceClass.DEGRADED,
    )
