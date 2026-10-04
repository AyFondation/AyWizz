# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/models.py
# Description: Pydantic v2 contracts for the object-grain document model of
#              310-SPEC-DOC-TRACEABILITY §4.1. An object is the smallest
#              addressable, individually reviewable unit of a container.
#
#              Two invariants are enforced HERE rather than in the service
#              layer, so that no caller — including the reindex path that
#              rebuilds objects from MinIO — can construct a violating
#              instance:
#                - R-310-008 figure/prose exclusivity;
#                - R-310-010 a version advances only with a review decision.
#
# @relation implements:R-310-004
# @relation implements:R-310-005
# @relation implements:R-310-006
# @relation implements:R-310-007
# @relation implements:R-310-008
# @relation implements:R-310-009
# @relation implements:R-310-010
# =============================================================================

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Identity — R-310-005
# ---------------------------------------------------------------------------

# Domain-agnostic per D-012 / R-300-090: the platform does not impose a
# vocabulary on container prefixes. `OBJ-1120`, `SEC-0304`, `T-SYS-118-01`
# are all valid; lowercase, spaces and empty segments are not.
_OBJECT_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")

_MAX_ID_LEN = 64


def is_valid_object_id(value: str) -> bool:
    """Return True when `value` is a well-formed object identifier.

    Args:
        value: Candidate identifier.

    Returns:
        True when the identifier matches the uppercase hyphen-segmented
        form and is within the length bound.
    """
    return len(value) <= _MAX_ID_LEN and _OBJECT_ID_RE.match(value) is not None


# ---------------------------------------------------------------------------
# Enumerations — closed sets per §4.1 of 310-SPEC
# ---------------------------------------------------------------------------


class ObjectType(StrEnum):
    """Closed set per R-310-004. Adding a member requires a spec amendment."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"


class ReviewState(StrEnum):
    """Closed set per R-310-006.

    AUTO_ACCEPTED is deliberately distinct from ACCEPTED (R-310-007): it
    records acceptance granted by cluster review, without individual human
    examination, and every coverage figure must be able to separate the two.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    AUTO_ACCEPTED = "auto-accepted"
    STALE = "stale"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    """The decisions that may advance an object version (R-310-010).

    CONFIRM_UNCHANGED is a first-class outcome, not the absence of one: it
    is what lets a closed change ticket distinguish "examined and found
    unaffected" from "never looked at" (R-310-150).
    """

    ACCEPT = "accept"
    AUTO_ACCEPT = "auto-accept"
    REJECT = "reject"
    CONFIRM_UNCHANGED = "confirm-unchanged"


class HolderKind(StrEnum):
    """Lock holder kind. Agents hold the same lock as humans (R-310-191)."""

    HUMAN = "human"
    AGENT = "agent"


#: Decisions that produce a new object version. A rejection does not: it
#: sends the working draft back to the agent without publishing anything.
_VERSIONING_DECISIONS: frozenset[ReviewDecision] = frozenset(
    {
        ReviewDecision.ACCEPT,
        ReviewDecision.AUTO_ACCEPT,
        ReviewDecision.CONFIRM_UNCHANGED,
    }
)

#: Review state reached by each versioning decision.
_DECISION_STATE: dict[ReviewDecision, ReviewState] = {
    ReviewDecision.ACCEPT: ReviewState.ACCEPTED,
    ReviewDecision.AUTO_ACCEPT: ReviewState.AUTO_ACCEPTED,
    ReviewDecision.CONFIRM_UNCHANGED: ReviewState.ACCEPTED,
    ReviewDecision.REJECT: ReviewState.REJECTED,
}


def state_for_decision(decision: ReviewDecision) -> ReviewState:
    """Return the review state a decision puts an object into.

    Args:
        decision: The review decision taken.

    Returns:
        The resulting review state.
    """
    return _DECISION_STATE[decision]


def advances_version(decision: ReviewDecision) -> bool:
    """Return True when a decision creates a new object version (R-310-010).

    Args:
        decision: The review decision taken.

    Returns:
        True for accept / auto-accept / confirm-unchanged; False for reject.
    """
    return decision in _VERSIONING_DECISIONS


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class ProducedBy(BaseModel):
    """Which workflow version produced an object (R-310-045).

    This stamp is what makes "the method changed — which artefacts were
    produced under the old version?" answerable.
    """

    model_config = ConfigDict(extra="forbid")

    workflow: str
    workflow_version: int = Field(ge=1)


class ReviewRecord(BaseModel):
    """The decision that produced the current object version (R-310-010).

    Every version carries one, which is what makes the version chain a
    history of decisions rather than of machine attempts.
    """

    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    actor: str
    at: datetime
    justification: str | None = None

    @model_validator(mode="after")
    def _confirm_unchanged_needs_justification(self) -> ReviewRecord:
        # R-310-150: a confirm-unchanged disposition is evidence that an
        # unmodified node was nonetheless examined. Evidence without a stated
        # reason is an assertion, so the justification is mandatory here and
        # optional elsewhere.
        if self.decision is ReviewDecision.CONFIRM_UNCHANGED and not (
            self.justification or ""
        ).strip():
            raise ValueError(
                "confirm-unchanged requires a justification (R-310-150)"
            )
        return self

    @field_validator("actor")
    @classmethod
    def _actor_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("actor SHALL NOT be blank (R-310-305)")
        return v


# ---------------------------------------------------------------------------
# The object itself — storage shape, E-310-001
# ---------------------------------------------------------------------------


class _ObjectBody(BaseModel):
    """Shared body/notation carrier enforcing R-310-008 exclusivity."""

    model_config = ConfigDict(extra="forbid")

    type: ObjectType
    body: str | None = None
    notation: str | None = None

    @model_validator(mode="after")
    def _figure_exclusivity(self) -> _ObjectBody:
        # R-310-008: a figure's authoritative content is its textual
        # description (`notation`), from which the SVG is rendered; the SVG
        # itself is never authoritative (R-310-003). Every other type carries
        # prose in `body`. Allowing both would create two sources of truth
        # for one object.
        if self.type is ObjectType.FIGURE:
            if not (self.notation or "").strip():
                raise ValueError(
                    "a figure SHALL carry a textual notation (R-310-008)"
                )
            if self.body is not None:
                raise ValueError(
                    "a figure SHALL NOT carry a prose body (R-310-008)"
                )
        else:
            if not (self.body or "").strip():
                raise ValueError(
                    f"a {self.type.value} SHALL carry a non-empty body (R-310-004)"
                )
            if self.notation is not None:
                raise ValueError(
                    f"a {self.type.value} SHALL NOT carry a notation (R-310-008)"
                )
        return self


class DocObject(_ObjectBody):
    """An object as persisted in MinIO — the source of truth (R-310-001).

    One JSON document per object at
    `projects/<pid>/docs/<container>/objects/<object-id>.json`.
    """

    object_id: str
    project_id: str
    container: str
    parent: str | None = None
    ordinal: int = Field(ge=0)
    version: int = Field(ge=1)
    review_state: ReviewState
    last_review: ReviewRecord | None = None
    produced_by: ProducedBy | None = None
    cycle_version: int | None = Field(default=None, ge=1)
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str

    @field_validator("object_id", "parent")
    @classmethod
    def _validate_ids(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not is_valid_object_id(v):
            raise ValueError(
                f"Invalid object id {v!r}: expected uppercase hyphen-segmented "
                "form such as 'OBJ-1120' (R-310-005)"
            )
        return v

    @model_validator(mode="after")
    def _version_requires_decision(self) -> DocObject:
        # R-310-010: a version exists only where a review decision was taken.
        # Version 1 is the sole exception — it is the object's first
        # publication, created by the accept that lifted its working draft,
        # and callers constructing a v1 for the first time may not yet have
        # the record. Any version beyond 1 without a decision record would be
        # an unattributable version, which R-310-305 forbids.
        if self.version > 1 and self.last_review is None:
            raise ValueError(
                "an object version beyond v1 SHALL carry the review decision "
                "that produced it (R-310-010)"
            )
        if self.last_review is not None and not advances_version(
            self.last_review.decision
        ):
            raise ValueError(
                "a rejected draft SHALL NOT produce an object version "
                "(R-310-010)"
            )
        return self

    @model_validator(mode="after")
    def _no_self_parent(self) -> DocObject:
        if self.parent is not None and self.parent == self.object_id:
            raise ValueError("an object SHALL NOT be its own parent")
        return self


class DocObjectPublic(BaseModel):
    """Object as exposed through the REST API.

    Withholds nothing of substance today, but exists as a separate contract
    so that storage-only fields added later (content hash, compressed size)
    do not leak across the boundary by default.
    """

    model_config = ConfigDict(extra="forbid")

    object_id: str
    container: str
    type: ObjectType
    parent: str | None = None
    ordinal: int
    version: int
    review_state: ReviewState
    body: str | None = None
    notation: str | None = None
    last_review: ReviewRecord | None = None
    produced_by: ProducedBy | None = None
    cycle_version: int | None = None
    has_working_draft: bool = False
    lock: ObjectLock | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str

    @classmethod
    def from_object(
        cls,
        obj: DocObject,
        *,
        has_working_draft: bool = False,
        lock: ObjectLock | None = None,
    ) -> DocObjectPublic:
        """Project a stored object onto the API contract.

        Args:
            obj: The stored object.
            has_working_draft: Whether an unresolved negotiation draft exists.
            lock: The lock currently held on the object, if any.

        Returns:
            The public representation.
        """
        return cls(
            object_id=obj.object_id,
            container=obj.container,
            type=obj.type,
            parent=obj.parent,
            ordinal=obj.ordinal,
            version=obj.version,
            review_state=obj.review_state,
            body=obj.body,
            notation=obj.notation,
            last_review=obj.last_review,
            produced_by=obj.produced_by,
            cycle_version=obj.cycle_version,
            has_working_draft=has_working_draft,
            lock=lock,
            created_at=obj.created_at,
            created_by=obj.created_by,
            updated_at=obj.updated_at,
            updated_by=obj.updated_by,
        )


# ---------------------------------------------------------------------------
# Working draft — R-310-009 / R-310-203
# ---------------------------------------------------------------------------


class WorkingDraftWrite(_ObjectBody):
    """Request body for writing a working draft.

    Writing a draft creates NO object version (R-310-009): converging on an
    acceptable rework takes several exchanges, and versioning each exchange
    fills the history with machine attempts carrying no decision.
    """

    negotiation_id: str
    base_version: int = Field(ge=1)

    @field_validator("negotiation_id")
    @classmethod
    def _negotiation_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("negotiation_id SHALL NOT be blank")
        return v


class WorkingDraft(WorkingDraftWrite):
    """A working draft as persisted, alongside but not inside the object.

    Overwritten on every iteration, and removed when the negotiation
    resolves by acceptance, rejection, or confirmation of no change
    (R-310-203).
    """

    object_id: str
    project_id: str
    container: str
    iteration: int = Field(ge=1)
    written_at: datetime
    written_by: str

    @field_validator("object_id")
    @classmethod
    def _validate_object_id(cls, v: str) -> str:
        if not is_valid_object_id(v):
            raise ValueError(f"Invalid object id {v!r} (R-310-005)")
        return v


class ObjectReviewRequest(BaseModel):
    """Request body resolving a negotiation with a review decision."""

    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    justification: str | None = None
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _confirm_unchanged_needs_justification(self) -> ObjectReviewRequest:
        if self.decision is ReviewDecision.CONFIRM_UNCHANGED and not (
            self.justification or ""
        ).strip():
            raise ValueError(
                "confirm-unchanged requires a justification (R-310-150)"
            )
        return self


# ---------------------------------------------------------------------------
# Locking — R-310-190…193
# ---------------------------------------------------------------------------


class ObjectLock(BaseModel):
    """An exclusive lease held on one object.

    A lock without expiry is held forever by an actor whose session ended,
    so the lease is mandatory (R-310-190). Because leases expire, the
    optimistic version check of R-310-192 remains the correctness guarantee;
    the lock only prevents wasted work.
    """

    model_config = ConfigDict(extra="forbid")

    object_id: str
    holder: str
    holder_kind: HolderKind
    acquired_at: datetime
    expires_at: datetime

    @field_validator("holder")
    @classmethod
    def _holder_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("holder SHALL NOT be blank (R-310-193)")
        return v

    @model_validator(mode="after")
    def _expiry_after_acquisition(self) -> ObjectLock:
        if self.expires_at <= self.acquired_at:
            raise ValueError("lock expiry SHALL be after acquisition (R-310-190)")
        return self

    def is_expired(self, now: datetime) -> bool:
        """Return True when the lease has lapsed at `now`.

        Args:
            now: The reference instant, timezone-aware.

        Returns:
            True when the lease no longer holds.
        """
        return now >= self.expires_at

    def is_held_by(self, actor: str, now: datetime) -> bool:
        """Return True when `actor` still holds this lease at `now`.

        Args:
            actor: Candidate holder identifier.
            now: The reference instant, timezone-aware.

        Returns:
            True when the lease is unexpired and belongs to `actor`.
        """
        return not self.is_expired(now) and self.holder == actor


class ObjectCreate(_ObjectBody):
    """Request body for proposing a new object.

    The result is `proposed` at v1 with no review record: an agent's
    proposal is not yet a decision (R-310-010).
    """

    object_id: str
    ordinal: int = Field(ge=0)
    parent: str | None = None
    produced_by: ProducedBy | None = None
    cycle_version: int | None = Field(default=None, ge=1)

    @field_validator("object_id", "parent")
    @classmethod
    def _validate_ids(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not is_valid_object_id(v):
            raise ValueError(f"Invalid object id {v!r} (R-310-005)")
        return v


class ObjectLockRequest(BaseModel):
    """Request body for taking the edit lease on an object."""

    model_config = ConfigDict(extra="forbid")

    holder_kind: HolderKind = HolderKind.HUMAN


# ---------------------------------------------------------------------------
# List envelopes — response shapes only, not registered contracts
# ---------------------------------------------------------------------------


class ObjectListResponse(BaseModel):
    """A container's objects, in reading order."""

    model_config = ConfigDict(extra="forbid")

    objects: list[DocObjectPublic] = Field(default_factory=list)


class ObjectVersionListResponse(BaseModel):
    """The retained version numbers of one object (R-310-202)."""

    model_config = ConfigDict(extra="forbid")

    object_id: str
    versions: list[int] = Field(default_factory=list)


# Resolve the forward reference used by DocObjectPublic.lock.
DocObjectPublic.model_rebuild()
ObjectListResponse.model_rebuild()
