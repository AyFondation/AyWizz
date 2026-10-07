# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/coverage/models.py
# Description: Pydantic v2 contracts for the traceability graph —
#              310-SPEC-DOC-TRACEABILITY §4.4 / §4.5 / §4.7.
#
#              Four invariants are structural here:
#
#              1. A requirement is covered only when EVERY one of its
#                 allocations is covered (R-310-064). Coverage is computed
#                 from the allocations, never set; a partial gap is a real,
#                 nameable state rather than a rounding of "covered".
#
#              2. Every allocation cites the `scope_id` of the container
#                 scope that justifies it (R-310-066). The format is checked
#                 here; existence against the published cycle is checked by
#                 the service, which is what holds the cycle.
#
#              3. "No allocation" is a verdict, not a silence (R-310-065):
#                 `out-of-project` carries a justification, and the absence
#                 of both allocation and verdict is what the audit flags.
#
#              4. A coverage link pins its target's version (R-310-145);
#                 staleness is computed against the target's current version,
#                 not stored, so it cannot drift from the truth.
#
#              `ReviewState` is imported from the object model rather than
#              redefined: an allocation and a coverage link move through the
#              same proposed → accepted lifecycle as an object, and a
#              parallel enum would be two vocabularies for one idea.
#
# @relation implements:R-310-064
# @relation implements:R-310-065
# @relation implements:R-310-066
# @relation implements:R-310-068
# @relation implements:R-310-096
# @relation implements:R-310-122
# @relation implements:R-310-145
# =============================================================================

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from ..objects.models import ReviewState

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

# A supplied requirement, or a fragment of one. The `/N` suffix is the
# distinct namespace of R-310-095: a fragment is visibly not something the
# issuing party wrote.
_REQUIREMENT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?:/[0-9]+)?$")
_SCOPE_ID_RE = re.compile(r"^SC-[0-9]{3,}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def is_valid_requirement_id(value: str) -> bool:
    """Return True for a requirement id such as `REQ-SYS-118` or `REQ-SYS-118/2`."""
    return len(value) <= 80 and _REQUIREMENT_ID_RE.match(value) is not None


def is_fragment(value: str) -> bool:
    """Return True when the identifier names a fragment of a split requirement."""
    return "/" in value


def parent_requirement(value: str) -> str:
    """Return the supplied requirement a fragment belongs to, or the id itself."""
    return value.split("/", 1)[0]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AllocationVerdict(StrEnum):
    """What was decided about where a requirement is answered (R-310-065)."""

    ALLOCATED = "allocated"
    OUT_OF_PROJECT = "out-of-project"
    """Explicitly not this project's to answer — a verdict, not a silence."""


class ReturnReason(StrEnum):
    """Why a container owner returned an allocation (R-310-068).

    Typed because each code routes to a different outcome: `out-of-scope`
    to re-allocation, `not-atomic` to splitting, `out-of-project` back to
    the issuer. A free-text reason could not be routed on, and would make
    the improvement signal of a recurring return unusable.
    """

    OUT_OF_SCOPE = "out-of-scope"
    NOT_ATOMIC = "not-atomic"
    OUT_OF_PROJECT = "out-of-project"


class CoverageStrength(StrEnum):
    """How well an object answers what it claims to cover (R-310-122)."""

    COVERED = "covered"
    WEAK = "weak"
    """Cites the requirement without answering it.

    A distinct state, not a flag on `covered`: an object that says "in
    accordance with REQ-118, the system brakes" declares coverage and
    demonstrates none. Folding it into `covered` is exactly what turns a
    coverage matrix green and untrustworthy.
    """


# ---------------------------------------------------------------------------
# Allocation — R-310-064 / R-310-065 / R-310-066
# ---------------------------------------------------------------------------


class Allocation(BaseModel):
    """One requirement allocated to one container.

    A requirement may carry several of these (R-310-064); its coverage is
    satisfied only when every one of them is covered.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    project_id: str
    container: str
    scope_id: str
    justification: str
    state: ReviewState = ReviewState.PROPOSED
    criticality: str | None = None
    decomposition_rationale: str | None = None
    actor: str
    at: datetime

    @field_validator("requirement_id")
    @classmethod
    def _valid_requirement(cls, v: str) -> str:
        if not is_valid_requirement_id(v):
            raise ValueError(f"Invalid requirement id {v!r}")
        return v

    @field_validator("container")
    @classmethod
    def _valid_container(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"Invalid container slug {v!r}")
        return v

    @field_validator("scope_id")
    @classmethod
    def _valid_scope(cls, v: str) -> str:
        # R-310-066: the citation is what makes an allocation reviewable.
        # Its existence in the published cycle is checked by the service.
        if not _SCOPE_ID_RE.match(v):
            raise ValueError(
                f"Invalid scope_id {v!r}: an allocation SHALL cite the "
                "container scope that justifies it (R-310-066)"
            )
        return v

    @field_validator("justification")
    @classmethod
    def _justification_is_substantive(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError(
                "an allocation SHALL justify itself; a citation with no "
                "reasoning cannot be reviewed (R-310-066)"
            )
        return v


class OutOfProjectVerdict(BaseModel):
    """An explicit statement that a requirement is not this project's to answer.

    R-310-065's escape valve. The failure mode it closes is silence: an
    allocating agent omitting what it cannot place. A verdict is reviewable;
    an omission is invisible.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    project_id: str
    justification: str
    actor: str
    at: datetime

    @field_validator("justification")
    @classmethod
    def _justification_required(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("an out-of-project verdict SHALL justify itself")
        return v


class AllocationRejection(BaseModel):
    """A container owner returning an allocation (R-310-068).

    Recorded rather than applied: erasing the allocation would leave the
    re-allocation agent no trace of the failure, so it would propose the
    same target again.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    project_id: str
    container: str
    reason: ReturnReason
    detail: str
    actor: str
    at: datetime

    @field_validator("detail")
    @classmethod
    def _detail_required(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError(
                "a return SHALL say why; the reason code routes it, the "
                "detail is what the next allocation reads (R-310-068)"
            )
        return v


# ---------------------------------------------------------------------------
# Coverage link — R-310-121 / R-310-122 / R-310-145
# ---------------------------------------------------------------------------


class CoverageLink(BaseModel):
    """An object answering a requirement, or an upstream object."""

    model_config = ConfigDict(extra="forbid")

    object_id: str
    project_id: str
    container: str
    target_id: str
    pinned_version: int = Field(ge=1)
    strength: CoverageStrength = CoverageStrength.COVERED
    state: ReviewState = ReviewState.PROPOSED
    actor: str
    at: datetime

    @field_validator("target_id")
    @classmethod
    def _valid_target(cls, v: str) -> str:
        if not is_valid_requirement_id(v):
            raise ValueError(f"Invalid coverage target {v!r}")
        return v

    def is_stale(self, current_target_version: int) -> bool:
        """Return True when the target has moved past the pinned version.

        Computed, never stored: a stored staleness flag drifts from the
        truth the moment the target changes without the flag being updated,
        which is the failure R-310-145 exists to prevent.

        Args:
            current_target_version: The target's version right now.

        Returns:
            True when the link needs re-review.
        """
        return current_target_version > self.pinned_version


# ---------------------------------------------------------------------------
# Aggregated coverage — R-310-064 / R-310-096
# ---------------------------------------------------------------------------


class AllocationCoverage(BaseModel):
    """Whether one allocation of one requirement is answered."""

    model_config = ConfigDict(extra="forbid")

    container: str
    allocation_state: ReviewState
    covering_objects: tuple[str, ...] = ()
    weak_objects: tuple[str, ...] = ()
    stale_objects: tuple[str, ...] = ()
    links: tuple[CoverageLink, ...] = ()
    """The links this classification was derived from.

    ADDED 2026-10-07. The three tuples above keep only OBJECT IDS, which
    discards `state`, `strength`, `actor` and `at` — and `R-500-019`
    requires the workbench to count auto-accepted requirements "from
    links actually marked auto-accepted, never inferred from a difference
    between totals", while `R-500-017` expands a link in place. Neither
    is derivable from an id. The links were already in `_classify`'s hand
    and thrown away, so this costs no extra query.

    Surfaced by `scripts/checks/audit_ui_api_chain.py`: the UI declared
    `AllocationCoverageView.links` all along and received `undefined`,
    so `allocation.links.some(…)` threw and took the workbench's
    `autoAcceptedIds` memo — and `LinkExpansion` — with it.
    """

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_covered(self) -> bool:
        """True when at least one accepted, non-weak, non-stale object answers it.

        A `computed_field`, not a plain `property`, so it REACHES THE WIRE:
        the UI rendered "not yet answered" unconditionally because
        `is_covered` was computed server-side and never serialised. This
        does not weaken `R-310-096` ("coverage is a conclusion, and a
        settable conclusion can disagree with its own evidence") — a
        computed field is output-only and still cannot be set.
        """
        return bool(self.covering_objects)


class RequirementCoverage(BaseModel):
    """Aggregated coverage of one requirement across all its allocations.

    Computed from the allocations and never settable (R-310-096): a
    requirement's coverage is a conclusion, and a settable conclusion can
    disagree with its own evidence.
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    allocations: tuple[AllocationCoverage, ...] = ()
    out_of_project: bool = False
    fragments: tuple[RequirementCoverage, ...] = ()

    @model_validator(mode="after")
    def _fragments_replace_allocations(self) -> RequirementCoverage:
        # R-310-096: a split requirement's coverage is the aggregate of its
        # fragments. Allowing both would create two answers to one question.
        if self.fragments and self.allocations:
            raise ValueError(
                "a split requirement is covered through its fragments, not "
                "through its own allocations (R-310-096)"
            )
        return self

    @property
    def is_covered(self) -> bool:
        """True when every allocation — or every fragment — is covered.

        R-310-064: coverage is satisfied only when EVERY allocation is
        satisfied. A requirement allocated to three containers and answered
        in two is not covered; it is partially covered, which is a different
        and worse thing to report as green.
        """
        if self.out_of_project:
            return True
        if self.fragments:
            return all(f.is_covered for f in self.fragments)
        if not self.allocations:
            return False
        return all(a.is_covered for a in self.allocations)

    @property
    def is_partial(self) -> bool:
        """True when some but not all of its allocations are answered."""
        if self.is_covered:
            return False
        units: tuple[RequirementCoverage, ...] | tuple[AllocationCoverage, ...] = (
            self.fragments or self.allocations
        )
        return any(u.is_covered for u in units)

    @property
    def is_unallocated(self) -> bool:
        """True when nothing was decided about this requirement (R-310-065)."""
        return not self.allocations and not self.fragments and not self.out_of_project


class ContainerCoverage(BaseModel):
    """What one container owes and what it has delivered."""

    model_config = ConfigDict(extra="forbid")

    container: str
    allocated: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    weak: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """True when no allocated requirement is still unanswered (R-310-120)."""
        return not self.uncovered


#: The review states that make an upstream a safe foundation. Only these
#: two: `R-310-007` keeps `auto-accepted` distinct from `accepted` for
#: counting, but both are acceptance — an object built on a cluster-accepted
#: upstream is not speculative.
ACCEPTED_STATES = frozenset({ReviewState.ACCEPTED, ReviewState.AUTO_ACCEPTED})


class SpeculativeMarking(BaseModel):
    """Whether one object was built on upstreams nobody has accepted yet.

    `R-310-177` v2. This is a marking about PROVENANCE, orthogonal to the
    object's own review state: end-to-end execution routinely produces an
    object that is both `accepted` and speculative, because a reviewer
    accepts the architecture object while the functional object above it is
    still `proposed`. A sixth review state would make that unrepresentable.

    Derived, never stored — the same reason `CoverageLink.is_stale` and
    `RequirementCoverage.is_covered` are computed: a stored flag would
    drift the moment an upstream was accepted without the flag being
    updated, which is the failure the rule exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    object_id: str
    container: str
    unaccepted_targets: tuple[str, ...] = ()
    """Upstreams this object answers that are neither `accepted` nor
    `auto-accepted`."""
    advanced_targets: tuple[str, ...] = ()
    """Of those, the ones that have moved past the version their coverage
    link pinned — the second clause of `R-310-177`."""

    @property
    def is_speculative(self) -> bool:
        """True when any upstream is not yet accepted."""
        return bool(self.unaccepted_targets)

    @property
    def must_become_stale(self) -> bool:
        """True when an unaccepted upstream changed before acceptance.

        `R-310-177`: the object's OWN review state must then be set to
        `stale`, because it answers a version of something that no longer
        exists and was never agreed to in the first place.
        """
        return bool(self.advanced_targets)

    def explain(self) -> str:
        """Return a reader-facing account of why this object is speculative."""
        if not self.is_speculative:
            return f"{self.object_id}: every upstream it answers is accepted"
        detail = (
            f"{self.object_id} in {self.container} answers "
            f"{list(self.unaccepted_targets)}, which nobody has accepted"
        )
        if self.must_become_stale:
            detail += (
                f"; {list(self.advanced_targets)} changed before acceptance, so "
                "it is stale (R-310-177)"
            )
        return detail


class SpeculativeListResponse(BaseModel):
    """Every speculative object in a project or container."""

    model_config = ConfigDict(extra="forbid")

    markings: tuple[SpeculativeMarking, ...]
    count: int = Field(ge=0)
    stale_count: int = Field(default=0, ge=0)
    """How many of them must become `stale` — the ones a reviewer has to
    look at now rather than eventually."""


RequirementCoverage.model_rebuild()


# ---------------------------------------------------------------------------
# REST request / response bodies
# ---------------------------------------------------------------------------
#
# Co-located with the contracts they serve, as in the object and process
# packages. A request body necessarily restates its contract's domain
# fields; keeping the two in one module is what makes that restatement
# reviewable side by side instead of drifting in another file.

class AllocateRequest(BaseModel):
    """Propose one allocation of a requirement to a container."""

    model_config = ConfigDict(extra="forbid")

    cycle_id: str
    requirement_id: str
    container: str
    scope_id: str
    justification: str
    criticality: str | None = None
    inherited_criticality: str | None = None
    decomposition_rationale: str | None = None
    override_exclusion: bool = False
    """Deliberately re-propose a container that already returned this.

    A blanket exclusion with no exit is the kind of rule people route
    around, so the override exists — visibly, and as a human act.
    """


class AcceptRequest(BaseModel):
    """Accept a proposed allocation, individually or by cluster."""

    model_config = ConfigDict(extra="forbid")

    auto: bool = False
    """True for cluster acceptance, recorded as `auto-accepted` (R-310-007)."""


class ReturnRequest(BaseModel):
    """Return an allocation to be re-decided (R-310-068)."""

    model_config = ConfigDict(extra="forbid")

    reason: ReturnReason
    detail: str


class VerdictRequest(BaseModel):
    """Declare a requirement not this project's to answer (R-310-065)."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    justification: str


class CoverRequest(BaseModel):
    """Record that an object answers a target."""

    model_config = ConfigDict(extra="forbid")

    object_id: str
    container: str
    target_id: str
    strength: CoverageStrength = CoverageStrength.COVERED


class AuditRequest(BaseModel):
    """Ask which of these requirements were never decided about."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[str] = Field(default_factory=list)


class ReturnResponse(BaseModel):
    """What happened when an allocation was returned."""

    model_config = ConfigDict(extra="forbid")

    ordinal: int
    escalated: bool
    next_exclusions: list[str] = Field(default_factory=list)


class SuspectLinkListResponse(BaseModel):
    """Coverage links whose target has moved past its pin (R-310-145)."""

    model_config = ConfigDict(extra="forbid")

    links: list[dict[str, Any]] = Field(default_factory=list)


class UnallocatedResponse(BaseModel):
    """Requirements with neither an allocation nor a verdict (R-310-065)."""

    model_config = ConfigDict(extra="forbid")

    unallocated: list[str] = Field(default_factory=list)
