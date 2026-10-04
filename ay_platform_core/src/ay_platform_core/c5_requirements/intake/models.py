# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/models.py
# Description: Contracts for intake of externally-supplied requirements and
#              their splitting — 310-SPEC §4.4 / §4.5.
#
#              FIVE INVARIANTS ARE STRUCTURAL HERE:
#
#              1. Every extracted requirement carries a RESOLVABLE anchor
#                 (R-310-062): file, location inside it, and the text
#                 interval. Without it a requirement cannot be traced back
#                 to what the customer actually sent, which is the first
#                 thing a contract review asks for.
#
#              2. A quality finding without a criterion identifier cannot be
#                 constructed (R-310-063). An LLM asked to review
#                 requirements produces plausible objections without limit;
#                 anchoring each to a declared criterion is what makes a
#                 false positive cheap to dismiss.
#
#              3. A fragment identifier is DERIVED from its parent, never
#                 supplied (R-310-095). Returning to a customer requirements
#                 it never wrote, numbered like its own, is a contractual
#                 confusion risk, so the namespace is visibly distinct by
#                 construction rather than by convention.
#
#              4. A split proposal MUST carry the failed atomicity finding
#                 that justifies it (R-310-093). Unconstrained splitting
#                 becomes the default reflex and fragments atomic
#                 requirements for no traceability gain.
#
#              5. A DEGRADED source (OCR) cannot be split or allocated until
#                 its extracted text is human-verified (R-310-061). Interval
#                 anchors are character offsets, and OCR misrecognition
#                 shifts them — so R-310-092 would pass against corrupted
#                 text and lose a clause invisibly.
#
# @relation implements:R-310-060
# @relation implements:R-310-061
# @relation implements:R-310-062
# @relation implements:R-310-063
# @relation implements:R-310-090
# @relation implements:R-310-093
# @relation implements:R-310-094
# @relation implements:R-310-095
# =============================================================================

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..coverage.models import is_valid_requirement_id
from .intervals import TextInterval, validate_partition

_CRITERION_RE = re.compile(r"^CRIT-[A-Z]{2,5}-[0-9]{3}$")
_DROP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


# ---------------------------------------------------------------------------
# Sources — R-310-060 / R-310-061
# ---------------------------------------------------------------------------


class SourceFormat(StrEnum):
    """Formats a customer drop may arrive in (R-300-080 v2)."""

    MD = "md"
    REQIF = "reqif"
    XLSX = "xlsx"
    DOCX = "docx"
    PDF = "pdf"


class SourceClass(StrEnum):
    """How much the extracted text can be trusted (R-310-060).

    Not cosmetic: the class decides whether interval anchors are usable at
    all, and therefore whether R-310-092 means anything on this source.
    """

    STRUCTURED = "structured"
    """Identifiers and fields are native to the format; anchors are exact."""

    TEXTUAL = "textual"
    """A reliable text layer; anchors are exact but structure is inferred."""

    DEGRADED = "degraded"
    """OCR. Character offsets are NOT reliable until a human confirms them."""


#: The format-to-class mapping of R-310-060. PDF is the only format whose
#: class depends on the file rather than the extension: a PDF with a text
#: layer is `textual`, one that needed OCR is `degraded`.
_FORMAT_CLASS: dict[SourceFormat, SourceClass] = {
    SourceFormat.MD: SourceClass.STRUCTURED,
    SourceFormat.REQIF: SourceClass.STRUCTURED,
    SourceFormat.XLSX: SourceClass.STRUCTURED,
    SourceFormat.DOCX: SourceClass.TEXTUAL,
    SourceFormat.PDF: SourceClass.TEXTUAL,
}


def default_source_class(
    source_format: SourceFormat, *, needed_ocr: bool = False
) -> SourceClass:
    """Return the confidence class of a source (R-310-060).

    Args:
        source_format: The format the drop arrived in.
        needed_ocr: True when the extractor had to OCR the file, which only
            a PDF can require.

    Returns:
        The class gating this source's downstream treatment.
    """
    if needed_ocr:
        return SourceClass.DEGRADED
    return _FORMAT_CLASS[source_format]


class SourceAnchor(BaseModel):
    """Where in the supplied document a requirement came from (R-310-062).

    `location` is the format's own addressing — a page and section for a
    PDF, a sheet and cell for a spreadsheet, a SPEC-OBJECT identifier for
    ReqIF — kept as the issuing party's own words so a contract review can
    follow it without a mapping table.
    """

    model_config = ConfigDict(extra="forbid")

    source_file: str
    location: str
    interval: TextInterval

    @field_validator("source_file", "location")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "an anchor SHALL name the file and the place inside it; an "
                "unresolvable anchor is no anchor (R-310-062)"
            )
        return v


class SuppliedRequirement(BaseModel):
    """One requirement as received, before anything is done to it.

    Never mutated (R-310-090): it is a contractual artefact belonging to its
    issuer, and rewriting it destroys the ability to show, at a contract
    review, what was received versus what was interpreted.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    project_id: str
    drop_id: str
    text: str
    anchor: SourceAnchor
    source_class: SourceClass
    criticality: str | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None
    received_at: datetime

    @field_validator("requirement_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not is_valid_requirement_id(v):
            raise ValueError(f"Invalid requirement id {v!r}")
        if "/" in v:
            raise ValueError(
                f"{v!r} is a fragment identifier; a supplied requirement is "
                "never in the fragment namespace (R-310-095)"
            )
        return v

    @field_validator("drop_id")
    @classmethod
    def _valid_drop(cls, v: str) -> str:
        if not _DROP_RE.match(v):
            raise ValueError(f"Invalid drop id {v!r}")
        return v

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a supplied requirement SHALL carry its text")
        return v

    @model_validator(mode="after")
    def _anchor_matches_the_text(self) -> SuppliedRequirement:
        # An anchor whose interval is longer than the text it anchors cannot
        # be resolved against the source, so it is not an anchor.
        if self.anchor.interval.length > len(self.text) and self.text:
            raise ValueError(
                f"anchor interval spans {self.anchor.interval.length} characters "
                f"but the requirement text is {len(self.text)} (R-310-062)"
            )
        return self

    @property
    def needs_verification(self) -> bool:
        """True when this source is degraded and nobody has confirmed it yet.

        R-310-061: until it is confirmed, splitting and allocation would
        operate on text whose character offsets are not trustworthy.
        """
        return self.source_class is SourceClass.DEGRADED and self.verified_by is None


# ---------------------------------------------------------------------------
# Quality review — R-310-063
# ---------------------------------------------------------------------------


class QualityFinding(BaseModel):
    """One defect found in a supplied requirement.

    The criterion identifier is mandatory and format-checked: a finding that
    cites no declared criterion is not surfaced to a reviewer at all, which
    is what keeps an LLM's unlimited supply of plausible objections from
    becoming the reviewer's problem.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    criterion_id: str
    detail: str
    actor: str
    at: datetime

    @field_validator("criterion_id")
    @classmethod
    def _valid_criterion(cls, v: str) -> str:
        if not _CRITERION_RE.match(v):
            raise ValueError(
                f"Invalid criterion id {v!r}: a finding SHALL cite a declared "
                "criterion such as 'CRIT-QUA-004' (R-310-063, E-310-005)"
            )
        return v

    @field_validator("detail")
    @classmethod
    def _detail_is_substantive(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError(
                "a finding SHALL say what is wrong, quoting the offending "
                "text where it can (R-310-063)"
            )
        return v


#: The criterion that, and only that, authorises a split (R-310-093).
ATOMICITY_CRITERION = "CRIT-QUA-001"


# ---------------------------------------------------------------------------
# Splitting — R-310-090 / R-310-093 / R-310-094 / R-310-095
# ---------------------------------------------------------------------------


def fragment_id(parent_id: str, ordinal: int) -> str:
    """Return the identifier of the n-th fragment of a requirement.

    Derived, never supplied (R-310-095): the namespace has to be visibly
    distinct from the issuer's own numbering, and a free-form id would
    eventually be made to look like one.

    Args:
        parent_id: The supplied requirement's identifier.
        ordinal: 1-based fragment number.

    Returns:
        The fragment identifier, e.g. `REQ-SYS-118/2`.
    """
    if ordinal < 1:
        raise ValueError(f"fragment ordinal SHALL be >= 1, got {ordinal}")
    if "/" in parent_id:
        raise ValueError(f"{parent_id!r} is already a fragment")
    return f"{parent_id}/{ordinal}"


class Fragment(BaseModel):
    """One sub-requirement of a split supplied requirement.

    Carries the interval it restates, not a copy of the parent's text: the
    parent is the source of truth and is never mutated (R-310-090).
    """

    model_config = ConfigDict(extra="forbid")

    fragment_id: str
    parent_id: str
    project_id: str
    ordinal: int = Field(ge=1)
    interval: TextInterval
    statement: str

    @model_validator(mode="after")
    def _id_is_derived_from_the_parent(self) -> Fragment:
        expected = fragment_id(self.parent_id, self.ordinal)
        if self.fragment_id != expected:
            raise ValueError(
                f"fragment id {self.fragment_id!r} is not derived from its "
                f"parent and ordinal (expected {expected!r}) — R-310-095"
            )
        return self

    @property
    def excluded_from_issuer_export(self) -> bool:
        """True always: a fragment is ours, not the issuing party's.

        A property rather than a stored flag so it cannot be set to False.
        Exporting fragments back to a customer under numbering resembling
        its own is the contractual confusion R-310-095 forbids.
        """
        return True


class SplitProposal(BaseModel):
    """A proposed split of one supplied requirement.

    Refused unless it is justified by a failed atomicity finding
    (R-310-093) and its intervals tile the source exactly (R-310-092).
    """

    model_config = ConfigDict(extra="forbid")

    parent_id: str
    project_id: str
    drop_id: str
    source_text: str
    fragments: tuple[Fragment, ...]
    atomicity_finding: QualityFinding
    actor: str
    at: datetime

    @model_validator(mode="after")
    def _split_is_justified_and_exhaustive(self) -> SplitProposal:
        if self.atomicity_finding.criterion_id != ATOMICITY_CRITERION:
            raise ValueError(
                "a split SHALL be justified by a failed atomicity check "
                f"({ATOMICITY_CRITERION}), not by "
                f"{self.atomicity_finding.criterion_id!r} — unconstrained "
                "splitting fragments atomic requirements for no gain "
                "(R-310-093)"
            )
        if self.atomicity_finding.requirement_id != self.parent_id:
            raise ValueError(
                "the atomicity finding SHALL concern the requirement being "
                "split"
            )
        if len(self.fragments) < 2:
            raise ValueError(
                "a split SHALL produce at least two fragments; one fragment "
                "is the requirement itself"
            )
        for fragment in self.fragments:
            if fragment.parent_id != self.parent_id:
                raise ValueError(
                    f"fragment {fragment.fragment_id!r} does not belong to "
                    f"{self.parent_id!r}"
                )

        report = validate_partition(
            tuple(f.interval for f in self.fragments), self.source_text
        )
        if not report.is_valid:
            raise ValueError(
                f"split does not tile the source text (R-310-092): "
                f"{report.explain()}"
            )
        return self

    @property
    def fragment_ids(self) -> tuple[str, ...]:
        """The identifiers this split creates, in order."""
        return tuple(f.fragment_id for f in sorted(self.fragments, key=lambda f: f.ordinal))


class ReworkRequest(BaseModel):
    """A defect sent back to the issuing party (R-310-094).

    Emitted alongside a split, never instead of it. A non-atomic
    requirement is a defect in the supplied specification; correcting it
    only internally absorbs the issuer's debt silently and leaves nothing
    to show at a contract review.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    project_id: str
    drop_id: str
    criterion_id: str
    detail: str
    anchor: SourceAnchor
    raised_by: str
    raised_at: datetime

    @field_validator("criterion_id")
    @classmethod
    def _valid_criterion(cls, v: str) -> str:
        if not _CRITERION_RE.match(v):
            raise ValueError(f"Invalid criterion id {v!r} (E-310-005)")
        return v


# ---------------------------------------------------------------------------
# REST request / response bodies — R-310-047 layout convention (DV-19)
# ---------------------------------------------------------------------------
#
# Co-located with the contracts they serve, as in the object, process and
# coverage packages. A request body necessarily restates its contract's
# domain fields; keeping the two in one module is what makes that
# restatement reviewable side by side instead of drifting in another file.


class FindingRequest(BaseModel):
    """Record one quality finding against a supplied requirement."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    detail: str


class SplitSpan(BaseModel):
    """One fragment's claimed span, as a caller states it."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=1)
    statement: str | None = None


class SplitRequest(BaseModel):
    """Propose a split of one supplied requirement.

    The spans must tile the requirement's text (R-310-092); the service
    refuses anything else, quoting the text that would be lost.
    """

    model_config = ConfigDict(extra="forbid")

    spans: tuple[SplitSpan, ...]

    @model_validator(mode="after")
    def _at_least_two_spans(self) -> SplitRequest:
        if len(self.spans) < 2:
            raise ValueError(
                "a split SHALL produce at least two fragments; one fragment "
                "is the requirement itself"
            )
        return self


class IngestResponse(BaseModel):
    """What one ingested drop produced."""

    model_config = ConfigDict(extra="forbid")

    drop_id: str
    source_format: SourceFormat
    source_class: SourceClass
    requirement_ids: list[str] = Field(default_factory=list)
    needs_verification: bool


class VerificationResponse(BaseModel):
    """Which requirements a human verification unblocked (R-310-061)."""

    model_config = ConfigDict(extra="forbid")

    cleared: list[str] = Field(default_factory=list)


class SuppliedRequirementListResponse(BaseModel):
    """The identifiers stored for one drop."""

    model_config = ConfigDict(extra="forbid")

    requirement_ids: list[str] = Field(default_factory=list)


class FindingListResponse(BaseModel):
    """Findings recorded against one supplied requirement."""

    model_config = ConfigDict(extra="forbid")

    findings: list[QualityFinding] = Field(default_factory=list)


class FragmentListResponse(BaseModel):
    """A requirement's fragments, empty when it was never split."""

    model_config = ConfigDict(extra="forbid")

    fragments: list[Fragment] = Field(default_factory=list)


class SplitResponse(BaseModel):
    """A split and the rework request it necessarily raised (R-310-094).

    Both on the wire for the same reason the service returns the pair: a
    client cannot be shown the split without what is owed to the issuer.
    """

    model_config = ConfigDict(extra="forbid")

    proposal: SplitProposal
    rework: ReworkRequest


class ReworkListResponse(BaseModel):
    """Everything a drop owes its issuing party."""

    model_config = ConfigDict(extra="forbid")

    requests: list[ReworkRequest] = Field(default_factory=list)
