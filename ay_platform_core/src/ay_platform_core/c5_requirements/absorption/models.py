# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/models.py
# Description: Change tickets, qualifications and dispositions —
#              310-SPEC §4.8 (R-310-146, R-310-148, R-310-149, R-310-150).
#
#              THERE IS NO STATUS FIELD. R-310-149 forbids exposing manual
#              status mutation of a change ticket. The strongest form of
#              that is not a guarded setter but the absence of the thing
#              to set: `status` is a property derived from `closed_at`, so
#              no request body, no row and no agent can carry a status.
#              For the same reason there is no `assignee`, no `priority`
#              and no `due_date` anywhere in this module — their absence
#              IS the requirement, which is why a test asserts it rather
#              than leaving it to be noticed.
#
#              A DISPOSITION IS A POSITIVE RECORD (R-310-150). Deciding a
#              node needs no change is a review outcome, and it is the
#              outcome that happens most often. So `confirmed-unchanged`
#              cannot be constructed without an actor, a timestamp and a
#              justification: otherwise a closed ticket could not tell
#              "examined and found unaffected" from "never looked at",
#              which is the entire value of closing it.
#
#              AND `modified-and-accepted` CANNOT BE CONSTRUCTED WITHOUT
#              THE VERSION IT PRODUCED. "Modified" is a claim about an
#              artifact; the version number is the observation backing it.
#              This is the platform's standing rule that gate evidence is
#              observed and never inferred (R-200-012), applied here.
#
#              THE FOURTH BLOCKER THAT ISN'T. R-310-146 names three
#              closure blockers. R-310-147 requires the split
#              exhaustiveness check to be re-run against modified text,
#              and a failed re-run means a new clause falls outside every
#              fragment — so it is reported as `uncovered-requirement`,
#              the third blocker, rather than as a fourth kind. A new
#              blocker kind would be a spec amendment (§8.1).
#
# @relation implements:R-310-146
# @relation implements:R-310-148
# @relation implements:R-310-149
# @relation implements:R-310-150
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .diff import ChangeKind
from .traversal import ImpactSet

#: Shortest justification accepted for a review decision. Long enough to
#: refuse "ok" and "n/a", short enough not to invite padding.
MIN_JUSTIFICATION = 12


class Qualification(StrEnum):
    """What an agent proposes a node's impact amounts to (`R-310-148`).

    Closed set: an agent that could invent a fifth label would be
    filtering impact by vocabulary, which `R-310-141` forbids.
    """

    NO_EFFECT = "no-effect"
    REWORDING = "rewording"
    SUBSTANTIVE_REWORK = "substantive-rework"
    ARCHITECTURE_DECISION_REQUIRED = "architecture-decision-required"


class DispositionKind(StrEnum):
    """How a reviewer disposed of one impacted node (`R-310-150`)."""

    MODIFIED_AND_ACCEPTED = "modified-and-accepted"
    CONFIRMED_UNCHANGED = "confirmed-unchanged"


class TicketStatus(StrEnum):
    """The two states a ticket can be observed in.

    Two, not three: an "in progress" state would be a hand-maintained
    claim about work, and `R-310-149` exists to keep the ticket's state a
    consequence of the graph rather than of someone's bookkeeping.
    """

    OPEN = "open"
    CLOSED = "closed"


class ClosureBlocker(StrEnum):
    """Why a ticket is not closable (`R-310-146`)."""

    UNDISPOSITIONED_NODE = "undispositioned-node"
    STALE_LINK = "stale-link"
    UNCOVERED_REQUIREMENT = "uncovered-requirement"


def change_ticket_id(project_id: str, drop_id: str, requirement_id: str) -> str:
    """Return the deterministic identifier of a change ticket.

    Derived rather than minted so `R-310-140`'s "exactly one ticket per
    requirement" holds by construction: re-running the same diff writes
    the same key instead of opening a second ticket.
    """
    return f"{project_id}:{drop_id}:{requirement_id}"


class QualificationProposal(BaseModel):
    """An agent's reading of one node's impact, and its human gate.

    `R-310-148` requires the qualification to be presented to a human
    gate *before any rework*. The gate is therefore recorded here, on the
    proposal, rather than inferred from whether rework happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    qualification: Qualification
    justification: str = Field(min_length=MIN_JUSTIFICATION)
    proposed_by: str = Field(min_length=1)
    proposed_at: datetime
    accepted_by: str | None = None
    accepted_at: datetime | None = None

    @model_validator(mode="after")
    def _gate_is_whole(self) -> QualificationProposal:
        """Refuse a half-recorded gate.

        An acceptance with no actor, or an actor with no instant, is not
        evidence of a review; it is a row that looks like one.
        """
        if (self.accepted_by is None) != (self.accepted_at is None):
            raise ValueError(
                "a human gate records both its actor and its instant, or "
                "neither (R-310-148)"
            )
        return self

    @property
    def is_gated(self) -> bool:
        """True once a human has accepted this qualification."""
        return self.accepted_by is not None


class Disposition(BaseModel):
    """How one impacted node was disposed of (`R-310-150`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(min_length=1)
    kind: DispositionKind
    actor: str = Field(min_length=1)
    at: datetime
    justification: str = Field(default="", max_length=4000)
    object_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _carries_its_evidence(self) -> Disposition:
        """Refuse a disposition that asserts a review without evidence of it.

        `confirmed-unchanged` needs a justification because it is the
        decision that nothing happened, and without one a closed ticket
        cannot distinguish it from an unexamined node. `modified-and-
        accepted` needs the version it produced, because "modified" is a
        claim about an artifact and the version is the observation.
        """
        if self.kind is DispositionKind.CONFIRMED_UNCHANGED:
            if len(self.justification.strip()) < MIN_JUSTIFICATION:
                raise ValueError(
                    "a 'confirmed-unchanged' disposition records why the node "
                    f"is unaffected, in at least {MIN_JUSTIFICATION} characters: "
                    "deciding a node needs no change is a review outcome, not "
                    "the absence of one (R-310-150)"
                )
            if self.object_version is not None:
                raise ValueError(
                    "a 'confirmed-unchanged' disposition cannot name a produced "
                    "version; nothing was produced (R-310-150)"
                )
        elif self.object_version is None:
            raise ValueError(
                "a 'modified-and-accepted' disposition names the object version "
                "it produced; 'modified' is a claim about an artifact and the "
                "version is the observation behind it (R-310-150)"
            )
        return self


class ClosureRefusal(BaseModel):
    """One reason a ticket cannot be closed, naming what failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocker: ClosureBlocker
    node_id: str | None = None
    detail: str = Field(min_length=1)


class ChangeTicket(BaseModel):
    """One supplied change, its impact set, and how it was absorbed.

    Opened by the mechanism from a diff, never by hand (`R-310-149`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    drop_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    kind: ChangeKind
    previous_text: str | None = None
    current_text: str | None = None
    opened_at: datetime
    impact: ImpactSet
    qualifications: tuple[QualificationProposal, ...] = ()
    dispositions: tuple[Disposition, ...] = ()
    closed_by: str | None = None
    closed_at: datetime | None = None

    @model_validator(mode="after")
    def _is_internally_consistent(self) -> ChangeTicket:
        """Refuse a ticket that could not have arisen from a real absorption."""
        if (self.closed_by is None) != (self.closed_at is None):
            raise ValueError("a closed ticket records both its actor and its instant")

        members = self.impact.node_ids
        for disposition in self.dispositions:
            if disposition.node_id not in members:
                raise ValueError(
                    f"{disposition.node_id!r} is dispositioned but is not in the "
                    "impact set; a disposition of a node outside the set is "
                    "review effort recorded against nothing"
                )
        seen = {disposition.node_id for disposition in self.dispositions}
        if len(seen) != len(self.dispositions):
            raise ValueError(
                "a node carries at most one disposition; two would leave a "
                "reviewer asking which is current (R-310-150)"
            )
        for proposal in self.qualifications:
            if proposal.node_id not in members:
                raise ValueError(
                    f"{proposal.node_id!r} is qualified but is not in the impact set"
                )
        if self.closed_at is not None and seen != set(members):
            raise ValueError(
                "a closed ticket has every node of its impact set dispositioned; "
                f"{sorted(set(members) - seen)} would be closed unexamined "
                "(R-310-146)"
            )
        return self

    @property
    def status(self) -> TicketStatus:
        """Derived, never stored, never settable (`R-310-149`)."""
        return TicketStatus.CLOSED if self.closed_at is not None else TicketStatus.OPEN

    @property
    def node_count(self) -> int:
        """The closure denominator — distinct nodes (`R-310-144`)."""
        return self.impact.node_count

    @property
    def dispositioned_count(self) -> int:
        """How many nodes have been reviewed."""
        return len(self.dispositions)

    @property
    def undispositioned_ids(self) -> tuple[str, ...]:
        """The nodes still awaiting a review outcome."""
        done = {disposition.node_id for disposition in self.dispositions}
        return tuple(sorted(self.impact.node_ids - done))

    @property
    def confirmed_unchanged_count(self) -> int:
        """How many nodes were examined and found unaffected.

        Reported in its own right because it is the evidence that an
        unmodified node was nonetheless looked at (`E-310-007`).
        """
        return sum(
            1
            for disposition in self.dispositions
            if disposition.kind is DispositionKind.CONFIRMED_UNCHANGED
        )

    def disposition_of(self, node_id: str) -> Disposition | None:
        """Return a node's disposition, or None when it has none yet."""
        for disposition in self.dispositions:
            if disposition.node_id == node_id:
                return disposition
        return None

    def qualification_of(self, node_id: str) -> QualificationProposal | None:
        """Return a node's qualification proposal, or None."""
        for proposal in self.qualifications:
            if proposal.node_id == node_id:
                return proposal
        return None


class ClosureReport(BaseModel):
    """The verdict of the closure gate (`R-310-146`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(min_length=1)
    node_count: int = Field(ge=0)
    dispositioned_count: int = Field(ge=0)
    refusals: tuple[ClosureRefusal, ...] = ()

    @property
    def is_closable(self) -> bool:
        """True when nothing blocks closure."""
        return not self.refusals

    @property
    def blockers(self) -> frozenset[ClosureBlocker]:
        """The distinct kinds of blocker standing in the way."""
        return frozenset(refusal.blocker for refusal in self.refusals)

    def explain(self) -> str:
        """Return a reader-facing account of why closure was refused."""
        if self.is_closable:
            return (
                f"{self.ticket_id}: closable — "
                f"{self.dispositioned_count}/{self.node_count} nodes dispositioned"
            )
        lines = [
            f"{self.ticket_id}: not closable "
            f"({self.dispositioned_count}/{self.node_count} nodes dispositioned)"
        ]
        lines.extend(
            f"  - {refusal.blocker.value}"
            + (f" [{refusal.node_id}]" if refusal.node_id else "")
            + f": {refusal.detail}"
            for refusal in self.refusals
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# REST bodies
#
# Note what is NOT here: no body creates a ticket, assigns one, sets a
# priority or writes a status. That absence is R-310-149 (T-310-009).
# ---------------------------------------------------------------------------


class QualifyRequest(BaseModel):
    """Propose a qualification for one impacted node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    qualification: Qualification
    justification: str = Field(min_length=MIN_JUSTIFICATION)


class AcceptQualificationRequest(BaseModel):
    """Pass the human gate on one node's qualification (`R-310-148`)."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)


class DispositionRequest(BaseModel):
    """Record how one impacted node was disposed of."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    kind: DispositionKind
    justification: str = Field(default="", max_length=4000)
    object_version: int | None = Field(default=None, ge=1)


class TicketListResponse(BaseModel):
    """A page of change tickets."""

    model_config = ConfigDict(extra="forbid")

    tickets: tuple[ChangeTicket, ...]
    count: int = Field(ge=0)


class AbsorbResponse(BaseModel):
    """What one re-supplied drop opened."""

    model_config = ConfigDict(extra="forbid")

    drop_id: str = Field(min_length=1)
    opened: tuple[str, ...]
    unchanged_count: int = Field(ge=0)
    reformatted_ids: tuple[str, ...] = ()
