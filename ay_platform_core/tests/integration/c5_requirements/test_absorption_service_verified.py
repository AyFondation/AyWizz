# =============================================================================
# File: test_absorption_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_absorption_service_verified.py
# Description: Integration tests for AbsorptionService against real MinIO +
#              ArangoDB. The coverage graph is real, so the impact set is
#              traversed out of actual stored edges rather than a stub.
#
#              What this tier establishes that no lower one can:
#                - a ticket's impact set comes out of the stored graph, and
#                  a requirement nothing answers yields an empty one;
#                - the closure gate refuses on each of its three
#                  preconditions INDEPENDENTLY, and names which failed
#                  (T-310-003);
#                - a ticket whose every node is `confirmed-unchanged`
#                  closes, and the closed form carries each actor,
#                  timestamp and justification (T-310-008);
#                - a modification recorded before its qualification passed
#                  a human gate is refused (R-310-148);
#                - a re-run of the split exhaustiveness check against the
#                  MODIFIED text blocks closure when a new clause falls
#                  outside every fragment (R-310-147).
#
# @relation validates:R-310-140
# @relation validates:R-310-145
# @relation validates:R-310-146
# @relation validates:R-310-147
# @relation validates:R-310-148
# @relation validates:R-310-149
# @relation validates:R-310-150
# @relation validates:T-310-003
# @relation validates:T-310-008
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.absorption.diff import (
    ChangeKind,
    RequirementSnapshot,
)
from ay_platform_core.c5_requirements.absorption.models import (
    ClosureBlocker,
    DispositionKind,
    Qualification,
    TicketStatus,
)
from ay_platform_core.c5_requirements.absorption.repository import (
    AbsorptionRepository,
)
from ay_platform_core.c5_requirements.absorption.service import (
    AbsorptionRefusedError,
    AbsorptionService,
    ClosureRefusedError,
    TicketNotFoundError,
)
from ay_platform_core.c5_requirements.absorption.storage import AbsorptionStorage
from ay_platform_core.c5_requirements.coverage.models import CoverageLink
from ay_platform_core.c5_requirements.coverage.repository import CoverageRepository
from ay_platform_core.c5_requirements.intake.intervals import TextInterval
from ay_platform_core.c5_requirements.intake.models import (
    ATOMICITY_CRITERION,
    Fragment,
    QualityFinding,
    SplitProposal,
    fragment_id,
)
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
PID = "adas"
DROP = "W14"
REQ = "CUST-001"
ARCH = "030-ARCH"
FUNC = "020-FUNC"
OWNER = "o.mathieu"
AGENT = "agent:qualifier"

_WAS = "On brake pedal release the system shall reduce deceleration to zero within 250 ms."
_NOW_TEXT = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 150 ms."
)
_WHY = "Re-read against the 150 ms bound; this object does not quote a timing."


class Versions:
    """Injected current-version lookup, driven by the test."""

    def __init__(self) -> None:
        self.table: dict[str, int] = {}

    async def __call__(self, project_id: str, target_id: str) -> int | None:
        return self.table.get(target_id)


class Coverage:
    """Injected coverage conclusion, driven by the test."""

    def __init__(self) -> None:
        self.covered = True

    async def __call__(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> bool:
        return self.covered


class Splits:
    """Injected split lookup, driven by the test."""

    def __init__(self) -> None:
        self.proposal: SplitProposal | None = None

    async def __call__(
        self, project_id: str, drop_id: str, requirement_id: str
    ) -> SplitProposal | None:
        return self.proposal


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[AbsorptionService, CoverageRepository, Versions, Coverage, Splits]]:
    db_name = f"c5_absorb_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        coverage_repo = CoverageRepository(db)
        coverage_repo._ensure_collections_sync()
        absorption_repo = AbsorptionRepository(db)
        absorption_repo._ensure_collections_sync()
        versions, coverage, splits = Versions(), Coverage(), Splits()
        service = AbsorptionService(
            AbsorptionStorage(c5_storage),
            absorption_repo,
            coverage_repo,
            versions,
            coverage,
            splits,
        )
        yield service, coverage_repo, versions, coverage, splits
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
def service(
    wired: tuple[AbsorptionService, CoverageRepository, Versions, Coverage, Splits],
) -> AbsorptionService:
    return wired[0]


@pytest.fixture
def graph(
    wired: tuple[AbsorptionService, CoverageRepository, Versions, Coverage, Splits],
) -> CoverageRepository:
    return wired[1]


@pytest.fixture
def versions(
    wired: tuple[AbsorptionService, CoverageRepository, Versions, Coverage, Splits],
) -> Versions:
    return wired[2]


@pytest.fixture
def coverage(
    wired: tuple[AbsorptionService, CoverageRepository, Versions, Coverage, Splits],
) -> Coverage:
    return wired[3]


@pytest.fixture
def splits(
    wired: tuple[AbsorptionService, CoverageRepository, Versions, Coverage, Splits],
) -> Splits:
    return wired[4]


async def _link(
    graph: CoverageRepository,
    object_id: str,
    target_id: str,
    container: str = ARCH,
    pinned: int = 1,
) -> None:
    await graph.put_coverage(
        CoverageLink(
            project_id=PID,
            object_id=object_id,
            container=container,
            target_id=target_id,
            pinned_version=pinned,
            actor=OWNER,
            at=NOW,
        )
    )


async def _absorb(service: AbsorptionService) -> object:
    _, tickets = await service.absorb(
        PID,
        DROP,
        previous=[RequirementSnapshot(requirement_id=REQ, text=_WAS)],
        current=[RequirementSnapshot(requirement_id=REQ, text=_NOW_TEXT)],
        now=NOW,
    )
    return tickets[0]


async def _confirm(service: AbsorptionService, node_id: str) -> None:
    await service.disposition(
        PID,
        DROP,
        REQ,
        node_id=node_id,
        kind=DispositionKind.CONFIRMED_UNCHANGED,
        actor=OWNER,
        justification=_WHY,
        now=NOW,
    )


# ---------------------------------------------------------------------------
# Opening from a diff — R-310-140 / R-310-141
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unchanged_drop_opens_nothing(service: AbsorptionService) -> None:
    difference, tickets = await service.absorb(
        PID,
        DROP,
        previous=[RequirementSnapshot(requirement_id=REQ, text=_WAS)],
        current=[RequirementSnapshot(requirement_id=REQ, text=_WAS)],
        now=NOW,
    )
    assert tickets == ()
    assert difference.is_empty


@pytest.mark.asyncio
async def test_a_modified_requirement_opens_one_ticket_with_its_impact_set(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "FD-010", REQ, FUNC)
    await _link(graph, "AD-100", "FD-010", ARCH)

    _, tickets = await service.absorb(
        PID,
        DROP,
        previous=[RequirementSnapshot(requirement_id=REQ, text=_WAS)],
        current=[RequirementSnapshot(requirement_id=REQ, text=_NOW_TEXT)],
        now=NOW,
    )

    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.ticket_id == f"{PID}:{DROP}:{REQ}"
    assert ticket.kind is ChangeKind.MODIFIED
    assert ticket.impact.node_ids == {"FD-010", "AD-100"}
    assert ticket.status is TicketStatus.OPEN


@pytest.mark.asyncio
async def test_re_absorbing_the_same_change_does_not_open_a_second_ticket(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    """R-310-140's 'exactly one', enforced by the derived key."""
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _absorb(service)

    tickets = await service.list_tickets(PID)
    assert len(tickets) == 1


@pytest.mark.asyncio
async def test_a_requirement_nothing_answers_opens_a_ticket_with_an_empty_set(
    service: AbsorptionService,
) -> None:
    """The ticket still exists: the change arrived and must be seen."""
    ticket = await _absorb(service)
    assert ticket.impact.is_empty  # type: ignore[attr-defined]
    assert ticket.node_count == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_node_reached_twice_is_in_the_set_once_with_both_paths(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "FD-010", REQ, FUNC)
    await _link(graph, "FD-011", REQ, FUNC)
    await _link(graph, "AD-100", "FD-010", ARCH)
    await _link(graph, "AD-100", "FD-011", ARCH)

    ticket = await _absorb(service)
    node = ticket.impact.node("AD-100")  # type: ignore[attr-defined]
    assert node.path_count == 2
    assert ticket.node_count == 3  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_withdrawn_requirement_opens_a_removal_ticket(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    _, tickets = await service.absorb(
        PID,
        DROP,
        previous=[RequirementSnapshot(requirement_id=REQ, text=_WAS)],
        current=[],
        now=NOW,
    )
    assert tickets[0].kind is ChangeKind.REMOVED
    assert tickets[0].impact.node_ids == {"AD-100"}


@pytest.mark.asyncio
async def test_an_indexed_ticket_is_listed_as_open(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    assert len(await service.list_tickets(PID, only_open=True)) == 1


@pytest.mark.asyncio
async def test_addressing_a_ticket_that_was_never_opened_is_refused(
    service: AbsorptionService,
) -> None:
    with pytest.raises(TicketNotFoundError, match="never on request"):
        await service.get_ticket(PID, DROP, "CUST-999")


# ---------------------------------------------------------------------------
# Qualification and its human gate — R-310-148
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_qualification_is_recorded_ungated(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    ticket = await service.qualify(
        PID,
        DROP,
        REQ,
        node_id="AD-100",
        qualification=Qualification.SUBSTANTIVE_REWORK,
        justification="The 150 ms bound invalidates the stated settling time.",
        actor=AGENT,
        now=NOW,
    )
    proposal = ticket.qualification_of("AD-100")
    assert proposal is not None
    assert proposal.is_gated is False


@pytest.mark.asyncio
async def test_re_qualifying_replaces_rather_than_accumulates(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    """Two live qualifications would leave a reviewer asking which is current."""
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    for qualification in (Qualification.NO_EFFECT, Qualification.REWORDING):
        ticket = await service.qualify(
            PID,
            DROP,
            REQ,
            node_id="AD-100",
            qualification=qualification,
            justification="Revised after reading the surrounding clause.",
            actor=AGENT,
            now=NOW,
        )
    assert len(ticket.qualifications) == 1
    assert ticket.qualifications[0].qualification is Qualification.REWORDING


@pytest.mark.asyncio
async def test_accepting_a_qualification_records_the_human_and_the_instant(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await service.qualify(
        PID, DROP, REQ, node_id="AD-100", qualification=Qualification.REWORDING,
        justification="Only the quoted bound changes; the obligation stands.",
        actor=AGENT, now=NOW,
    )
    ticket = await service.accept_qualification(
        PID, DROP, REQ, node_id="AD-100", actor=OWNER, now=NOW
    )
    proposal = ticket.qualification_of("AD-100")
    assert proposal is not None
    assert proposal.is_gated is True
    assert proposal.accepted_by == OWNER


@pytest.mark.asyncio
async def test_accepting_a_qualification_that_was_never_proposed_is_refused(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    with pytest.raises(AbsorptionRefusedError, match="no qualification to accept"):
        await service.accept_qualification(
            PID, DROP, REQ, node_id="AD-100", actor=OWNER, now=NOW
        )


@pytest.mark.asyncio
async def test_qualifying_a_node_outside_the_impact_set_is_refused(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    """R-310-141: the set is computed, and recording cannot extend it."""
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    with pytest.raises(AbsorptionRefusedError, match="not in the impact set"):
        await service.qualify(
            PID, DROP, REQ, node_id="TD-900",
            qualification=Qualification.NO_EFFECT,
            justification="Unrelated to the timing clause that changed.",
            actor=AGENT, now=NOW,
        )


# ---------------------------------------------------------------------------
# Disposition — R-310-148 / R-310-150
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_modification_before_its_human_gate_is_refused(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    """R-310-148 requires the gate BEFORE any rework; a recorded
    modification is evidence rework happened."""
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await service.qualify(
        PID, DROP, REQ, node_id="AD-100",
        qualification=Qualification.SUBSTANTIVE_REWORK,
        justification="The stated settling time no longer holds.",
        actor=AGENT, now=NOW,
    )
    with pytest.raises(AbsorptionRefusedError, match="passing a human gate"):
        await service.disposition(
            PID, DROP, REQ, node_id="AD-100",
            kind=DispositionKind.MODIFIED_AND_ACCEPTED,
            actor=OWNER, object_version=3, now=NOW,
        )


@pytest.mark.asyncio
async def test_a_modification_with_no_qualification_at_all_is_refused(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    with pytest.raises(AbsorptionRefusedError, match="passing a human gate"):
        await service.disposition(
            PID, DROP, REQ, node_id="AD-100",
            kind=DispositionKind.MODIFIED_AND_ACCEPTED,
            actor=OWNER, object_version=3, now=NOW,
        )


@pytest.mark.asyncio
async def test_a_modification_after_its_gate_is_recorded_with_its_version(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await service.qualify(
        PID, DROP, REQ, node_id="AD-100",
        qualification=Qualification.SUBSTANTIVE_REWORK,
        justification="The stated settling time no longer holds.",
        actor=AGENT, now=NOW,
    )
    await service.accept_qualification(
        PID, DROP, REQ, node_id="AD-100", actor=OWNER, now=NOW
    )
    ticket = await service.disposition(
        PID, DROP, REQ, node_id="AD-100",
        kind=DispositionKind.MODIFIED_AND_ACCEPTED,
        actor=OWNER, object_version=3, now=NOW,
    )
    disposition = ticket.disposition_of("AD-100")
    assert disposition is not None
    assert disposition.object_version == 3


@pytest.mark.asyncio
async def test_confirmed_unchanged_needs_no_prior_gate(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    """Deciding nothing needs doing IS the human gate; a second would be
    ceremony rather than control."""
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    ticket = await service.get_ticket(PID, DROP, REQ)
    assert ticket.confirmed_unchanged_count == 1


@pytest.mark.asyncio
async def test_re_dispositioning_replaces_rather_than_accumulates(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    await _confirm(service, "AD-100")
    ticket = await service.get_ticket(PID, DROP, REQ)
    assert ticket.dispositioned_count == 1


# ---------------------------------------------------------------------------
# The closure gate — R-310-146 (T-310-003, T-310-008)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_undispositioned_node_refuses_closure_and_is_named(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _link(graph, "AD-101", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")

    report = await service.closure_report(PID, DROP, REQ)
    assert report.is_closable is False
    assert report.blockers == {ClosureBlocker.UNDISPOSITIONED_NODE}
    assert "AD-101" in report.explain()


@pytest.mark.asyncio
async def test_a_stale_link_refuses_closure_and_names_both_versions(
    service: AbsorptionService, graph: CoverageRepository, versions: Versions
) -> None:
    await _link(graph, "AD-100", REQ, pinned=2)
    await _absorb(service)
    await _confirm(service, "AD-100")
    versions.table[REQ] = 5

    report = await service.closure_report(PID, DROP, REQ)
    assert report.blockers == {ClosureBlocker.STALE_LINK}
    assert "pins version 2" in report.explain()
    assert "now at 5" in report.explain()


@pytest.mark.asyncio
async def test_a_link_pinned_at_the_current_version_is_not_stale(
    service: AbsorptionService, graph: CoverageRepository, versions: Versions
) -> None:
    await _link(graph, "AD-100", REQ, pinned=5)
    await _absorb(service)
    await _confirm(service, "AD-100")
    versions.table[REQ] = 5

    report = await service.closure_report(PID, DROP, REQ)
    assert report.is_closable is True


@pytest.mark.asyncio
async def test_a_requirement_left_uncovered_refuses_closure(
    service: AbsorptionService, graph: CoverageRepository, coverage: Coverage
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    coverage.covered = False

    report = await service.closure_report(PID, DROP, REQ)
    assert report.blockers == {ClosureBlocker.UNCOVERED_REQUIREMENT}
    assert "was absorbed when it was dropped" in report.explain()


@pytest.mark.asyncio
async def test_every_blocker_is_reported_at_once_not_one_per_attempt(
    service: AbsorptionService,
    graph: CoverageRepository,
    versions: Versions,
    coverage: Coverage,
) -> None:
    """A reviewer told only the first of three obstacles comes back twice more."""
    await _link(graph, "AD-100", REQ, pinned=1)
    await _link(graph, "AD-101", REQ, pinned=1)
    await _absorb(service)
    await _confirm(service, "AD-100")
    versions.table[REQ] = 9
    coverage.covered = False

    report = await service.closure_report(PID, DROP, REQ)
    assert report.blockers == {
        ClosureBlocker.UNDISPOSITIONED_NODE,
        ClosureBlocker.STALE_LINK,
        ClosureBlocker.UNCOVERED_REQUIREMENT,
    }


@pytest.mark.asyncio
async def test_closing_a_refused_ticket_raises_carrying_the_report(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    with pytest.raises(ClosureRefusedError) as raised:
        await service.close(PID, DROP, REQ, actor=OWNER, now=NOW)
    assert raised.value.report.blockers == {ClosureBlocker.UNDISPOSITIONED_NODE}


@pytest.mark.asyncio
async def test_a_ticket_every_node_confirmed_unchanged_closes(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    """T-310-008's positive half, end to end."""
    await _link(graph, "AD-100", REQ)
    await _link(graph, "AD-101", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    await _confirm(service, "AD-101")

    closed = await service.close(PID, DROP, REQ, actor=OWNER, now=NOW)
    assert closed.status is TicketStatus.CLOSED
    assert closed.closed_by == OWNER
    assert closed.confirmed_unchanged_count == 2
    for disposition in closed.dispositions:
        assert disposition.actor == OWNER
        assert disposition.at == NOW
        assert disposition.justification == _WHY


@pytest.mark.asyncio
async def test_closing_is_snapshotted_so_the_signed_off_form_survives(
    service: AbsorptionService, graph: CoverageRepository, c5_storage: RequirementsStorage
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    await service.close(PID, DROP, REQ, actor=OWNER, now=NOW)

    snapshot = await AbsorptionStorage(c5_storage).get_closure_snapshot(PID, DROP, REQ)
    assert snapshot is not None
    assert snapshot.status is TicketStatus.CLOSED


@pytest.mark.asyncio
async def test_closing_an_already_closed_ticket_is_idempotent(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    first = await service.close(PID, DROP, REQ, actor=OWNER, now=NOW)
    second = await service.close(PID, DROP, REQ, actor="someone.else", now=NOW)
    assert second.closed_by == first.closed_by


@pytest.mark.asyncio
async def test_a_disposition_after_closure_is_refused(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    await service.close(PID, DROP, REQ, actor=OWNER, now=NOW)

    with pytest.raises(AbsorptionRefusedError, match="is closed"):
        await _confirm(service, "AD-100")


@pytest.mark.asyncio
async def test_a_ticket_with_an_empty_impact_set_closes_on_coverage_alone(
    service: AbsorptionService,
) -> None:
    """Nothing to review, but the requirement must still be answered."""
    await _absorb(service)
    closed = await service.close(PID, DROP, REQ, actor=OWNER, now=NOW)
    assert closed.status is TicketStatus.CLOSED


# ---------------------------------------------------------------------------
# R-310-147 — the split exhaustiveness check re-run against the NEW text
# ---------------------------------------------------------------------------


# The split case needs an agglomerated requirement: a one-fragment split is
# the requirement itself and SplitProposal refuses it, correctly.
_AGG = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms, and limit the torque ramp to 140 Nm/s."
)
_BOUNDARY = _AGG.index(", and limit")
_AGG_SPANS = (TextInterval(0, _BOUNDARY), TextInterval(_BOUNDARY, len(_AGG)))

#: Same length, so the existing fragment intervals still tile it.
_AGG_FASTER = _AGG.replace("250 ms", "150 ms")
#: A clause appended beyond the last fragment — answered by nobody.
_AGG_PLUS = _AGG + " The torque ramp shall be logged."


def _split(text: str, spans: tuple[TextInterval, ...]) -> SplitProposal:
    finding = QualityFinding(
        requirement_id=REQ,
        criterion_id=ATOMICITY_CRITERION,
        detail="Two independent obligations in one sentence.",
        actor=AGENT,
        at=NOW,
    )
    return SplitProposal(
        parent_id=REQ,
        project_id=PID,
        drop_id=DROP,
        source_text=text,
        fragments=tuple(
            Fragment(
                fragment_id=fragment_id(REQ, ordinal),
                parent_id=REQ,
                project_id=PID,
                ordinal=ordinal,
                interval=span,
                statement=span.slice_of(text),
            )
            for ordinal, span in enumerate(spans, start=1)
        ),
        atomicity_finding=finding,
        actor=AGENT,
        at=NOW,
    )


async def _absorb_agglomerated(service: AbsorptionService, current: str) -> None:
    """Absorb a change to the agglomerated requirement that was split."""
    await service.absorb(
        PID,
        DROP,
        previous=[RequirementSnapshot(requirement_id=REQ, text=_AGG)],
        current=[RequirementSnapshot(requirement_id=REQ, text=current)],
        now=NOW,
    )


@pytest.mark.asyncio
async def test_fragments_that_no_longer_tile_the_modified_text_block_closure(
    service: AbsorptionService, graph: CoverageRepository, splits: Splits
) -> None:
    """A clause appended past the last fragment is answered by nobody.

    Exactly the failure R-310-147's rationale names: re-checking only the
    fragments would miss it, silently.
    """
    await _link(graph, "AD-100", REQ)
    await _absorb_agglomerated(service, _AGG_PLUS)
    await _confirm(service, "AD-100")
    # The split was cut against the ORIGINAL text, as it would have been.
    splits.proposal = _split(_AGG, _AGG_SPANS)

    report = await service.closure_report(PID, DROP, REQ)
    assert report.blockers == {ClosureBlocker.UNCOVERED_REQUIREMENT}
    assert "no longer tile the modified text" in report.explain()
    assert "logged" in report.explain()


@pytest.mark.asyncio
async def test_fragments_that_still_tile_the_modified_text_do_not_block(
    service: AbsorptionService, graph: CoverageRepository, splits: Splits
) -> None:
    """A modification inside a fragment leaves the tiling intact."""
    await _link(graph, "AD-100", REQ)
    await _absorb_agglomerated(service, _AGG_FASTER)
    await _confirm(service, "AD-100")
    splits.proposal = _split(_AGG, _AGG_SPANS)

    report = await service.closure_report(PID, DROP, REQ)
    assert report.is_closable is True


@pytest.mark.asyncio
async def test_an_unsplit_requirement_skips_the_exhaustiveness_recheck(
    service: AbsorptionService, graph: CoverageRepository
) -> None:
    await _link(graph, "AD-100", REQ)
    await _absorb(service)
    await _confirm(service, "AD-100")
    report = await service.closure_report(PID, DROP, REQ)
    assert report.is_closable is True
