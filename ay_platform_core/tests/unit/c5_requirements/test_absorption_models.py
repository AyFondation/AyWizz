# =============================================================================
# File: test_absorption_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c5_requirements/test_absorption_models.py
# Description: Change tickets, qualifications and dispositions —
#              R-310-146, R-310-148, R-310-149, R-310-150.
#
# @relation validates:R-310-146
# @relation validates:R-310-148
# @relation validates:R-310-149
# @relation validates:R-310-150
# @relation validates:T-310-008
# @relation validates:T-310-009
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ay_platform_core.c5_requirements.absorption.diff import ChangeKind
from ay_platform_core.c5_requirements.absorption.models import (
    MIN_JUSTIFICATION,
    ChangeTicket,
    ClosureBlocker,
    ClosureRefusal,
    ClosureReport,
    Disposition,
    DispositionKind,
    Qualification,
    QualificationProposal,
    TicketStatus,
    change_ticket_id,
)
from ay_platform_core.c5_requirements.absorption.traversal import (
    ImpactEdge,
    ImpactNode,
    ImpactPath,
    ImpactSet,
)

_NOW = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
_WHY = "Reviewed against the new 150 ms bound; the ramp limit is untouched."


def _node(node_id: str, container: str = "architecture") -> ImpactNode:
    return ImpactNode(
        node_id=node_id,
        container=container,
        causes=(
            ImpactEdge(source_id="CUST-001", node_id=node_id, container=container),
        ),
        paths=(ImpactPath(nodes=("CUST-001", node_id)),),
    )


def _impact(*node_ids: str) -> ImpactSet:
    return ImpactSet(
        seed_id="CUST-001", nodes=tuple(_node(node_id) for node_id in node_ids)
    )


def _ticket(**overrides: object) -> ChangeTicket:
    base: dict[str, object] = {
        "ticket_id": change_ticket_id("adas", "W14", "CUST-001"),
        "project_id": "adas",
        "drop_id": "W14",
        "requirement_id": "CUST-001",
        "kind": ChangeKind.MODIFIED,
        "previous_text": "within 250 ms",
        "current_text": "within 150 ms",
        "opened_at": _NOW,
        "impact": _impact("AD-100"),
    }
    base.update(overrides)
    return ChangeTicket.model_validate(base)


def _disposed(
    node_id: str, kind: DispositionKind = DispositionKind.CONFIRMED_UNCHANGED
) -> Disposition:
    if kind is DispositionKind.CONFIRMED_UNCHANGED:
        return Disposition(
            node_id=node_id, kind=kind, actor="olivier", at=_NOW, justification=_WHY
        )
    return Disposition(
        node_id=node_id, kind=kind, actor="olivier", at=_NOW, object_version=4
    )


# ---------------------------------------------------------------------------
# R-310-149 — the absence of the fields is the requirement (T-310-009)
# ---------------------------------------------------------------------------


def test_a_ticket_has_no_field_a_human_could_manage_it_with() -> None:
    """R-310-149 at model level: there is nothing to assign or prioritise."""
    fields = set(ChangeTicket.model_fields)
    assert fields.isdisjoint(
        {"status", "assignee", "assigned_to", "priority", "severity", "due_date", "owner"}
    )


def test_status_is_derived_from_closure_and_cannot_be_supplied() -> None:
    with pytest.raises(ValidationError):
        _ticket(status="closed")


def test_an_open_ticket_reports_open() -> None:
    assert _ticket().status is TicketStatus.OPEN


def test_a_closed_ticket_reports_closed() -> None:
    ticket = _ticket(
        dispositions=(_disposed("AD-100"),), closed_by="olivier", closed_at=_NOW
    )
    assert ticket.status is TicketStatus.CLOSED


def test_a_ticket_identifier_is_derived_so_a_rerun_cannot_duplicate_it() -> None:
    assert change_ticket_id("adas", "W14", "CUST-001") == "adas:W14:CUST-001"


def test_half_a_closure_record_is_refused() -> None:
    with pytest.raises(ValidationError, match="both its actor and its instant"):
        _ticket(dispositions=(_disposed("AD-100"),), closed_by="olivier")


# ---------------------------------------------------------------------------
# R-310-150 — a disposition carries its own evidence
# ---------------------------------------------------------------------------


def test_confirmed_unchanged_without_a_justification_cannot_exist() -> None:
    """The failure this forbids: a closed ticket that cannot distinguish
    'examined and found unaffected' from 'never looked at'."""
    with pytest.raises(ValidationError, match="records why the node"):
        Disposition(
            node_id="AD-100",
            kind=DispositionKind.CONFIRMED_UNCHANGED,
            actor="olivier",
            at=_NOW,
        )


def test_a_token_justification_is_refused() -> None:
    with pytest.raises(ValidationError, match=str(MIN_JUSTIFICATION)):
        Disposition(
            node_id="AD-100",
            kind=DispositionKind.CONFIRMED_UNCHANGED,
            actor="olivier",
            at=_NOW,
            justification="n/a",
        )


def test_whitespace_does_not_satisfy_the_justification_requirement() -> None:
    with pytest.raises(ValidationError, match="records why the node"):
        Disposition(
            node_id="AD-100",
            kind=DispositionKind.CONFIRMED_UNCHANGED,
            actor="olivier",
            at=_NOW,
            justification="   " * 10,
        )


def test_confirmed_unchanged_records_its_actor_and_instant() -> None:
    disposition = _disposed("AD-100")
    assert disposition.actor == "olivier"
    assert disposition.at == _NOW
    assert disposition.justification == _WHY


def test_confirmed_unchanged_cannot_claim_a_produced_version() -> None:
    with pytest.raises(ValidationError, match="nothing was produced"):
        Disposition(
            node_id="AD-100",
            kind=DispositionKind.CONFIRMED_UNCHANGED,
            actor="olivier",
            at=_NOW,
            justification=_WHY,
            object_version=4,
        )


def test_modified_and_accepted_without_a_version_cannot_exist() -> None:
    """'Modified' is a claim about an artifact; the version is the observation."""
    with pytest.raises(ValidationError, match="names the object version"):
        Disposition(
            node_id="AD-100",
            kind=DispositionKind.MODIFIED_AND_ACCEPTED,
            actor="olivier",
            at=_NOW,
        )


def test_modified_and_accepted_needs_no_justification() -> None:
    disposition = _disposed("AD-100", DispositionKind.MODIFIED_AND_ACCEPTED)
    assert disposition.justification == ""
    assert disposition.object_version == 4


def test_a_version_below_one_is_refused() -> None:
    with pytest.raises(ValidationError):
        Disposition(
            node_id="AD-100",
            kind=DispositionKind.MODIFIED_AND_ACCEPTED,
            actor="olivier",
            at=_NOW,
            object_version=0,
        )


# ---------------------------------------------------------------------------
# R-310-148 — the human gate is recorded, not inferred
# ---------------------------------------------------------------------------


def test_a_qualification_needs_a_justification() -> None:
    with pytest.raises(ValidationError):
        QualificationProposal(
            node_id="AD-100",
            qualification=Qualification.NO_EFFECT,
            justification="fine",
            proposed_by="agent:reviewer",
            proposed_at=_NOW,
        )


def test_a_fresh_qualification_is_not_gated() -> None:
    proposal = QualificationProposal(
        node_id="AD-100",
        qualification=Qualification.SUBSTANTIVE_REWORK,
        justification="The 150 ms bound invalidates the stated settling time.",
        proposed_by="agent:reviewer",
        proposed_at=_NOW,
    )
    assert proposal.is_gated is False


def test_a_gated_qualification_names_its_human_and_instant() -> None:
    proposal = QualificationProposal(
        node_id="AD-100",
        qualification=Qualification.REWORDING,
        justification="Only the quoted bound changes; the obligation stands.",
        proposed_by="agent:reviewer",
        proposed_at=_NOW,
        accepted_by="olivier",
        accepted_at=_NOW,
    )
    assert proposal.is_gated is True


def test_an_acceptance_without_an_instant_is_refused() -> None:
    with pytest.raises(ValidationError, match="both its actor and its instant"):
        QualificationProposal(
            node_id="AD-100",
            qualification=Qualification.NO_EFFECT,
            justification="The clause this object answers was not touched.",
            proposed_by="agent:reviewer",
            proposed_at=_NOW,
            accepted_by="olivier",
        )


def test_an_instant_without_an_actor_is_refused() -> None:
    with pytest.raises(ValidationError, match="both its actor and its instant"):
        QualificationProposal(
            node_id="AD-100",
            qualification=Qualification.NO_EFFECT,
            justification="The clause this object answers was not touched.",
            proposed_by="agent:reviewer",
            proposed_at=_NOW,
            accepted_at=_NOW,
        )


def test_the_four_qualifications_are_the_whole_set() -> None:
    assert {q.value for q in Qualification} == {
        "no-effect",
        "rewording",
        "substantive-rework",
        "architecture-decision-required",
    }


def test_an_invented_qualification_is_refused() -> None:
    """Validated through `model_validate`, which is the real arrival path.

    An invented label reaches the platform as JSON from an agent, not as a
    Python literal — and going through the parser also keeps the test
    honest under `mypy --strict` without a suppression.
    """
    with pytest.raises(ValidationError):
        QualificationProposal.model_validate(
            {
                "node_id": "AD-100",
                "qualification": "probably-fine",
                "justification": "The clause this object answers was not touched.",
                "proposed_by": "agent:reviewer",
                "proposed_at": _NOW,
            }
        )


# ---------------------------------------------------------------------------
# Ticket consistency
# ---------------------------------------------------------------------------


def test_a_disposition_of_a_node_outside_the_set_is_refused() -> None:
    with pytest.raises(ValidationError, match="not in the impact set"):
        _ticket(dispositions=(_disposed("TD-999"),))


def test_a_qualification_of_a_node_outside_the_set_is_refused() -> None:
    proposal = QualificationProposal(
        node_id="TD-999",
        qualification=Qualification.NO_EFFECT,
        justification="The clause this object answers was not touched.",
        proposed_by="agent:reviewer",
        proposed_at=_NOW,
    )
    with pytest.raises(ValidationError, match="not in the impact set"):
        _ticket(qualifications=(proposal,))


def test_two_dispositions_for_one_node_are_refused() -> None:
    with pytest.raises(ValidationError, match="at most one disposition"):
        _ticket(dispositions=(_disposed("AD-100"), _disposed("AD-100")))


def test_closing_with_an_undispositioned_node_is_refused_by_the_model() -> None:
    """T-310-008's negative half, enforced where it cannot be bypassed."""
    with pytest.raises(ValidationError, match="closed unexamined"):
        _ticket(
            impact=_impact("AD-100", "AD-101"),
            dispositions=(_disposed("AD-100"),),
            closed_by="olivier",
            closed_at=_NOW,
        )


def test_a_ticket_closed_on_confirmed_unchanged_alone_is_valid() -> None:
    """T-310-008's positive half: 'nothing changed' is a complete answer."""
    ticket = _ticket(
        impact=_impact("AD-100", "AD-101"),
        dispositions=(_disposed("AD-100"), _disposed("AD-101")),
        closed_by="olivier",
        closed_at=_NOW,
    )
    assert ticket.status is TicketStatus.CLOSED
    assert ticket.confirmed_unchanged_count == 2
    assert all(d.justification == _WHY for d in ticket.dispositions)


def test_progress_counts_nodes_and_names_what_is_left() -> None:
    ticket = _ticket(
        impact=_impact("AD-100", "AD-101", "TD-900"),
        dispositions=(_disposed("AD-101"),),
    )
    assert ticket.node_count == 3
    assert ticket.dispositioned_count == 1
    assert ticket.undispositioned_ids == ("AD-100", "TD-900")


def test_a_node_accessor_returns_none_rather_than_inventing_a_record() -> None:
    ticket = _ticket(dispositions=())
    assert ticket.disposition_of("AD-100") is None
    assert ticket.qualification_of("AD-100") is None


def test_accessors_find_what_is_there() -> None:
    proposal = QualificationProposal(
        node_id="AD-100",
        qualification=Qualification.NO_EFFECT,
        justification="The clause this object answers was not touched.",
        proposed_by="agent:reviewer",
        proposed_at=_NOW,
    )
    ticket = _ticket(dispositions=(_disposed("AD-100"),), qualifications=(proposal,))
    disposition = ticket.disposition_of("AD-100")
    assert disposition is not None
    assert disposition.kind is DispositionKind.CONFIRMED_UNCHANGED
    assert ticket.qualification_of("AD-100") == proposal


def test_a_ticket_round_trips_through_its_serialised_form() -> None:
    ticket = _ticket(dispositions=(_disposed("AD-100"),))
    assert ChangeTicket.model_validate(ticket.model_dump()) == ticket


# ---------------------------------------------------------------------------
# The closure report — R-310-146
# ---------------------------------------------------------------------------


def test_a_report_with_no_refusal_is_closable() -> None:
    report = ClosureReport(ticket_id="adas:W14:CUST-001", node_count=2, dispositioned_count=2)
    assert report.is_closable is True
    assert "closable" in report.explain()
    assert "2/2" in report.explain()


def test_a_refused_report_names_every_blocker_and_its_node() -> None:
    report = ClosureReport(
        ticket_id="adas:W14:CUST-001",
        node_count=3,
        dispositioned_count=1,
        refusals=(
            ClosureRefusal(
                blocker=ClosureBlocker.UNDISPOSITIONED_NODE,
                node_id="AD-100",
                detail="no disposition recorded",
            ),
            ClosureRefusal(
                blocker=ClosureBlocker.STALE_LINK,
                node_id="TD-900",
                detail="pinned version 3, target now at 4",
            ),
        ),
    )
    assert report.is_closable is False
    assert report.blockers == {
        ClosureBlocker.UNDISPOSITIONED_NODE,
        ClosureBlocker.STALE_LINK,
    }
    explained = report.explain()
    assert "not closable" in explained
    assert "undispositioned-node [AD-100]" in explained
    assert "stale-link [TD-900]" in explained
    assert "pinned version 3, target now at 4" in explained


def test_a_blocker_with_no_node_renders_without_an_empty_bracket() -> None:
    report = ClosureReport(
        ticket_id="adas:W14:CUST-001",
        node_count=1,
        dispositioned_count=1,
        refusals=(
            ClosureRefusal(
                blocker=ClosureBlocker.UNCOVERED_REQUIREMENT,
                detail="CUST-001 is answered by nothing after the change",
            ),
        ),
    )
    assert "[]" not in report.explain()
    assert "uncovered-requirement:" in report.explain()


def test_the_three_blockers_are_the_whole_set() -> None:
    """A fourth kind would be a spec amendment, not an implementation choice."""
    assert {b.value for b in ClosureBlocker} == {
        "undispositioned-node",
        "stale-link",
        "uncovered-requirement",
    }
