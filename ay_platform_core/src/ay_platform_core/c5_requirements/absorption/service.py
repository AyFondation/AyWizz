# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/service.py
# Description: Change absorption as machinery — 310-SPEC §4.8 (D-026).
#
#              THE RULE THIS MODULE EXISTS FOR. A ticket is created,
#              propagated and closed by the traceability mechanism, never
#              by a person (`R-310-149`). So there is no `open_ticket`
#              taking a human's description: `absorb` takes two sets of
#              supplied requirements and the tickets fall out of the diff.
#              The only operations a person has are the two that ARE
#              review: accepting a qualification, and dispositioning a
#              node.
#
#              CLOSURE IS A GATE, NOT A STATE TRANSITION. `close` does not
#              set a field; it evaluates `R-310-146` and refuses, naming
#              which precondition failed. A caller cannot close a ticket
#              by writing to it, because the model has nothing to write
#              (see `models.py`).
#
#              THE THREE BLOCKERS, AND WHERE EACH COMES FROM:
#
#              undispositioned-node   the ticket itself (R-310-150)
#              stale-link             an impact edge whose pinned version
#                                     is behind its target's current one
#                                     (R-310-145)
#              uncovered-requirement  the coverage conclusion for the
#                                     changed requirement, PLUS a failed
#                                     re-run of the split exhaustiveness
#                                     check (R-310-147) — a new clause
#                                     outside every fragment is a clause
#                                     answered by nobody, which is what
#                                     this blocker means
#
#              WHY R-310-147 IS RE-RUN AT CLOSURE AND NOT AT INTAKE. The
#              check needs the NEW source text and the EXISTING fragment
#              intervals, and the point of re-running it is to refuse
#              closure while a clause is unallocated. Running it earlier
#              would produce a warning nobody is obliged to act on;
#              running it here makes it binding.
#
#              DEPENDENCIES ARE INJECTED AS LOOKUPS, not as modules: this
#              service asks "what version is this at", "is this covered",
#              "was this split", and binding those to concrete repositories
#              here would make the gate untestable without a database — the
#              same reason `coverage/service.py` injects `VersionLookup`.
#
# @relation implements:R-310-140
# @relation implements:R-310-145
# @relation implements:R-310-146
# @relation implements:R-310-147
# @relation implements:R-310-148
# @relation implements:R-310-149
# @relation implements:R-310-150
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime

from ..coverage.repository import CoverageRepository
from ..intake.intervals import TextInterval, validate_partition
from ..intake.models import SplitProposal
from .diff import RequirementSnapshot, SourceDiff, SuppliedChange, diff_supplied
from .models import (
    ChangeTicket,
    ClosureBlocker,
    ClosureRefusal,
    ClosureReport,
    Disposition,
    DispositionKind,
    Qualification,
    QualificationProposal,
    change_ticket_id,
)
from .repository import AbsorptionRepository
from .storage import AbsorptionStorage
from .traversal import ImpactEdge, ImpactSet, traverse

#: (project_id, node_id) -> the node's current version, or None when the
#: node is not a versioned object (a supplied requirement, say).
VersionLookup = Callable[[str, str], Awaitable[int | None]]

#: (project_id, drop_id, requirement_id) -> whether it is still answered.
#: The drop is part of the question because a split requirement is covered
#: through its fragments (R-310-096) and fragments live under their drop.
CoverageLookup = Callable[[str, str, str], Awaitable[bool]]

#: (project_id, drop_id, requirement_id) -> its split, when it was split.
SplitLookup = Callable[[str, str, str], Awaitable[SplitProposal | None]]


class AbsorptionRefusedError(RuntimeError):
    """Raised when an absorption step cannot proceed as requested."""


class TicketNotFoundError(RuntimeError):
    """Raised when a ticket is addressed that was never opened."""


class ClosureRefusedError(RuntimeError):
    """Raised when closure is requested but `R-310-146` refuses it."""

    def __init__(self, report: ClosureReport) -> None:
        self.report = report
        super().__init__(report.explain())


class AbsorptionService:
    """Open, propagate and close change tickets.

    Args:
        storage: Source-of-truth persistence for tickets.
        repository: The queryable index.
        coverage: Owner of `req_object_edges`, read for the traversal.
        current_version: Version lookup backing the staleness blocker.
        is_covered: Coverage conclusion for the changed requirement.
        split_of: A requirement's split, when it has one.
    """

    def __init__(
        self,
        storage: AbsorptionStorage,
        repository: AbsorptionRepository,
        coverage: CoverageRepository,
        current_version: VersionLookup,
        is_covered: CoverageLookup,
        split_of: SplitLookup,
    ) -> None:
        self._storage = storage
        self._repo = repository
        self._coverage = coverage
        self._version = current_version
        self._is_covered = is_covered
        self._split_of = split_of

    # ------------------------------------------------------------------
    # Opening — R-310-140, R-310-141
    # ------------------------------------------------------------------

    async def absorb(
        self,
        project_id: str,
        drop_id: str,
        *,
        previous: Iterable[RequirementSnapshot],
        current: Iterable[RequirementSnapshot],
        now: datetime | None = None,
    ) -> tuple[SourceDiff, tuple[ChangeTicket, ...]]:
        """Diff a re-supplied drop and open exactly one ticket per change.

        Returns the diff alongside the tickets so the caller can report
        what did NOT change — the reformatted-only requirements in
        particular, which are deliberately ticketless and would otherwise
        vanish without trace.

        Raises:
            DiffError: When either side repeats a requirement id.
            GraphDefectError: When the coverage graph contains a cycle.
        """
        moment = now or datetime.now(UTC)
        difference = diff_supplied(previous, current)
        tickets = [
            await self._open(project_id, drop_id, change, moment)
            for change in difference.changes
        ]
        return difference, tuple(tickets)

    async def _open(
        self,
        project_id: str,
        drop_id: str,
        change: SuppliedChange,
        moment: datetime,
    ) -> ChangeTicket:
        """Open one ticket, computing its impact set by traversal."""
        impact = await self.impact_of(project_id, change.requirement_id)
        ticket = ChangeTicket(
            ticket_id=change_ticket_id(project_id, drop_id, change.requirement_id),
            project_id=project_id,
            drop_id=drop_id,
            requirement_id=change.requirement_id,
            kind=change.kind,
            previous_text=change.previous_text,
            current_text=change.current_text,
            opened_at=moment,
            impact=impact,
        )
        await self._persist(ticket)
        return ticket

    async def impact_of(self, project_id: str, requirement_id: str) -> ImpactSet:
        """Return the deterministic impact set of one requirement.

        Exposed in its own right so a reviewer can see the blast radius of
        a prospective change before it is absorbed.
        """

        async def layer(sources: frozenset[str]) -> Sequence[ImpactEdge]:
            rows = await self._coverage.links_to_targets(project_id, sources)
            # The inversion lives here, not in the coverage repository: a
            # stored link says "this object answers that target", and the
            # claim that impact therefore flows target -> object is change
            # absorption's, not storage's.
            return [
                ImpactEdge(
                    source_id=row["target_id"],
                    node_id=row["object_id"],
                    container=row["container"],
                    pinned_version=row.get("pinned_version", 1),
                )
                for row in rows
            ]

        return await traverse(requirement_id, layer)

    # ------------------------------------------------------------------
    # Qualification — R-310-148
    # ------------------------------------------------------------------

    async def qualify(
        self,
        project_id: str,
        drop_id: str,
        requirement_id: str,
        *,
        node_id: str,
        qualification: Qualification,
        justification: str,
        actor: str,
        now: datetime | None = None,
    ) -> ChangeTicket:
        """Record an agent's reading of one node's impact.

        Raises:
            TicketNotFoundError: When no ticket was opened.
            AbsorptionRefusedError: When the node is not in the impact set.
        """
        ticket = await self._load(project_id, drop_id, requirement_id)
        self._require_member(ticket, node_id)
        proposal = QualificationProposal(
            node_id=node_id,
            qualification=qualification,
            justification=justification,
            proposed_by=actor,
            proposed_at=now or datetime.now(UTC),
        )
        kept = tuple(
            existing
            for existing in ticket.qualifications
            if existing.node_id != node_id
        )
        updated = ticket.model_copy(update={"qualifications": (*kept, proposal)})
        await self._persist(updated)
        return updated

    async def accept_qualification(
        self,
        project_id: str,
        drop_id: str,
        requirement_id: str,
        *,
        node_id: str,
        actor: str,
        now: datetime | None = None,
    ) -> ChangeTicket:
        """Pass the human gate on one node's qualification (`R-310-148`).

        Raises:
            TicketNotFoundError: When no ticket was opened.
            AbsorptionRefusedError: When the node has no qualification yet.
        """
        ticket = await self._load(project_id, drop_id, requirement_id)
        proposal = ticket.qualification_of(node_id)
        if proposal is None:
            raise AbsorptionRefusedError(
                f"{node_id!r} has no qualification to accept; the gate of "
                "R-310-148 is passed on a proposal, and accepting nothing "
                "would record a review of nothing"
            )
        moment = now or datetime.now(UTC)
        gated = proposal.model_copy(
            update={"accepted_by": actor, "accepted_at": moment}
        )
        kept = tuple(p for p in ticket.qualifications if p.node_id != node_id)
        updated = ticket.model_copy(update={"qualifications": (*kept, gated)})
        await self._persist(updated)
        return updated

    # ------------------------------------------------------------------
    # Disposition — R-310-150
    # ------------------------------------------------------------------

    async def disposition(
        self,
        project_id: str,
        drop_id: str,
        requirement_id: str,
        *,
        node_id: str,
        kind: DispositionKind,
        actor: str,
        justification: str = "",
        object_version: int | None = None,
        now: datetime | None = None,
    ) -> ChangeTicket:
        """Record how one impacted node was disposed of.

        A `modified-and-accepted` disposition is refused unless the node's
        qualification passed its human gate first: `R-310-148` requires the
        qualification to be presented to a gate BEFORE any rework, and a
        recorded modification is evidence that rework happened. A
        `confirmed-unchanged` disposition needs no prior gate, because
        deciding that nothing needs doing IS the human gate — requiring a
        second one would be ceremony, not control.

        Raises:
            TicketNotFoundError: When no ticket was opened.
            AbsorptionRefusedError: When the node is outside the impact
                set, when a modification precedes its gate, or when the
                ticket is already closed.
        """
        ticket = await self._load(project_id, drop_id, requirement_id)
        if ticket.closed_at is not None:
            raise AbsorptionRefusedError(
                f"{ticket.ticket_id!r} is closed; a disposition recorded after "
                "closure would change the evidence the closure asserted"
            )
        self._require_member(ticket, node_id)

        if kind is DispositionKind.MODIFIED_AND_ACCEPTED:
            proposal = ticket.qualification_of(node_id)
            if proposal is None or not proposal.is_gated:
                raise AbsorptionRefusedError(
                    f"{node_id!r} was modified without its qualification passing "
                    "a human gate; R-310-148 requires the gate before any rework"
                )

        record = Disposition(
            node_id=node_id,
            kind=kind,
            actor=actor,
            at=now or datetime.now(UTC),
            justification=justification,
            object_version=object_version,
        )
        kept = tuple(d for d in ticket.dispositions if d.node_id != node_id)
        updated = ticket.model_copy(update={"dispositions": (*kept, record)})
        await self._persist(updated)
        return updated

    # ------------------------------------------------------------------
    # The closure gate — R-310-146 / R-310-147
    # ------------------------------------------------------------------

    async def closure_report(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> ClosureReport:
        """Evaluate `R-310-146` without changing anything.

        Raises:
            TicketNotFoundError: When no ticket was opened.
        """
        ticket = await self._load(project_id, drop_id, requirement_id)
        return await self._evaluate(ticket)

    async def close(
        self,
        project_id: str,
        drop_id: str,
        requirement_id: str,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> ChangeTicket:
        """Close a ticket if and only if `R-310-146` permits it.

        Raises:
            TicketNotFoundError: When no ticket was opened.
            ClosureRefusedError: When any precondition fails; the report it
                carries names which.
        """
        ticket = await self._load(project_id, drop_id, requirement_id)
        if ticket.closed_at is not None:
            return ticket
        report = await self._evaluate(ticket)
        if not report.is_closable:
            raise ClosureRefusedError(report)
        moment = now or datetime.now(UTC)
        closed = ticket.model_copy(
            update={"closed_by": actor, "closed_at": moment}
        )
        await self._persist(closed)
        return closed

    async def _evaluate(self, ticket: ChangeTicket) -> ClosureReport:
        """Collect every reason this ticket cannot close.

        Every blocker is collected rather than returning at the first:
        a reviewer told only the first of three obstacles will come back
        twice more.
        """
        refusals: list[ClosureRefusal] = []

        for node_id in ticket.undispositioned_ids:
            refusals.append(
                ClosureRefusal(
                    blocker=ClosureBlocker.UNDISPOSITIONED_NODE,
                    node_id=node_id,
                    detail=(
                        "no disposition recorded; a node must be either modified "
                        "and accepted, or positively confirmed unchanged "
                        "(R-310-150)"
                    ),
                )
            )

        refusals.extend(await self._stale_refusals(ticket))
        refusals.extend(await self._coverage_refusals(ticket))

        return ClosureReport(
            ticket_id=ticket.ticket_id,
            node_count=ticket.node_count,
            dispositioned_count=ticket.dispositioned_count,
            refusals=tuple(refusals),
        )

    async def _stale_refusals(
        self, ticket: ChangeTicket
    ) -> list[ClosureRefusal]:
        """Refuse closure while any impact edge pins a superseded version."""
        refusals: list[ClosureRefusal] = []
        seen: set[tuple[str, str]] = set()
        for node in ticket.impact.nodes:
            for cause in node.causes:
                key = (node.node_id, cause.source_id)
                if key in seen:
                    continue
                seen.add(key)
                current = await self._version(ticket.project_id, cause.source_id)
                if current is not None and current > cause.pinned_version:
                    refusals.append(
                        ClosureRefusal(
                            blocker=ClosureBlocker.STALE_LINK,
                            node_id=node.node_id,
                            detail=(
                                f"its link to {cause.source_id!r} pins version "
                                f"{cause.pinned_version}, which is now at "
                                f"{current}: the link was not re-examined "
                                "against what it claims to answer (R-310-145)"
                            ),
                        )
                    )
        return refusals

    async def _coverage_refusals(
        self, ticket: ChangeTicket
    ) -> list[ClosureRefusal]:
        """Refuse closure while the change left a clause answered by nobody."""
        refusals: list[ClosureRefusal] = []

        split = await self._split_of(
            ticket.project_id, ticket.drop_id, ticket.requirement_id
        )
        if split is not None and ticket.current_text is not None:
            report = validate_partition(
                tuple(
                    TextInterval(fragment.interval.start, fragment.interval.end)
                    for fragment in split.fragments
                ),
                ticket.current_text,
            )
            if not report.is_valid:
                refusals.append(
                    ClosureRefusal(
                        blocker=ClosureBlocker.UNCOVERED_REQUIREMENT,
                        node_id=None,
                        detail=(
                            "the existing fragments no longer tile the modified "
                            f"text, so a clause is allocated to nobody "
                            f"(R-310-147): {report.explain()}"
                        ),
                    )
                )
                return refusals

        if not await self._is_covered(
            ticket.project_id, ticket.drop_id, ticket.requirement_id
        ):
            refusals.append(
                ClosureRefusal(
                    blocker=ClosureBlocker.UNCOVERED_REQUIREMENT,
                    node_id=None,
                    detail=(
                        f"{ticket.requirement_id!r} is no longer answered by any "
                        "accepted object; closing would record that a supplied "
                        "requirement was absorbed when it was dropped "
                        "(R-310-146)"
                    ),
                )
            )
        return refusals

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_ticket(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> ChangeTicket:
        """Return one ticket.

        Raises:
            TicketNotFoundError: When none was opened.
        """
        return await self._load(project_id, drop_id, requirement_id)

    async def list_tickets(
        self, project_id: str, *, only_open: bool = False
    ) -> tuple[ChangeTicket, ...]:
        """Return a project's tickets, oldest first."""
        rows = await self._repo.list_changes(project_id, only_open=only_open)
        tickets: list[ChangeTicket] = []
        for row in rows:
            ticket = await self._storage.get_ticket(
                project_id, row["drop_id"], row["requirement_id"]
            )
            if ticket is not None:
                tickets.append(ticket)
        return tuple(tickets)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> ChangeTicket:
        ticket = await self._storage.get_ticket(project_id, drop_id, requirement_id)
        if ticket is None:
            raise TicketNotFoundError(
                f"no change ticket for {requirement_id!r} in drop {drop_id!r}; "
                "tickets are opened by absorbing a diff, never on request "
                "(R-310-149)"
            )
        return ticket

    async def _persist(self, ticket: ChangeTicket) -> None:
        await self._storage.put_ticket(ticket)
        await self._repo.put_change(ticket)

    @staticmethod
    def _require_member(ticket: ChangeTicket, node_id: str) -> None:
        if node_id not in ticket.impact.node_ids:
            raise AbsorptionRefusedError(
                f"{node_id!r} is not in the impact set of {ticket.ticket_id!r}; "
                "the impact set is computed by traversal and cannot be extended "
                "by recording something against a node outside it (R-310-141)"
            )
