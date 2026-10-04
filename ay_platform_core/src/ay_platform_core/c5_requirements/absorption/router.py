# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/router.py
# Description: REST surface for change absorption — 310-SPEC §4.8.
#
#              READ THE ROUTE LIST FOR WHAT IS ABSENT. There is no
#              POST /changes creating a ticket, no PATCH setting a status,
#              no assignee and no priority route. That absence is
#              `R-310-149`, and `T-310-009` asserts it rather than trusting
#              it: a ticket exists because a supplied requirement changed,
#              its scope is the traversal result, and its closure condition
#              is mechanical. A hand-managed work item beside that would be
#              a second, divergent source of truth about what remains to be
#              done — and would let a ticket be closed without the evidence
#              that closing it is supposed to constitute.
#
#              `POST /absorb` is the one route that opens tickets, and it
#              does not take a ticket: it takes the two sets of supplied
#              requirements and the tickets fall out of the diff.
#
#              `POST /close` is a GATE, not a status write. It evaluates
#              R-310-146 and answers 409 naming the precondition that
#              failed. `GET /closure` is the same evaluation without the
#              side effect, so a reviewer can see what is left before
#              trying.
#
#              Role gates:
#                - absorbing a drop is an owner decision: it opens review
#                  obligations across containers the caller does not own;
#                - qualifying is agent/editor work;
#                - ACCEPTING a qualification and DISPOSITIONING a node are
#                  owner decisions, because each is the human gate itself
#                  (R-310-148, R-310-150) — delegating them to anyone who
#                  can edit would dissolve the gate;
#                - closing is an owner decision for the same reason.
#              Reads are merely authenticated: a team cannot review what it
#              cannot see.
#
# @relation implements:R-310-140
# @relation implements:R-310-146
# @relation implements:R-310-148
# @relation implements:R-310-149
# @relation implements:R-310-150
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from .diff import DiffError, RequirementSnapshot
from .models import (
    AbsorbResponse,
    AcceptQualificationRequest,
    ChangeTicket,
    ClosureReport,
    DispositionRequest,
    QualifyRequest,
    TicketListResponse,
)
from .service import (
    AbsorptionRefusedError,
    AbsorptionService,
    ClosureRefusedError,
    TicketNotFoundError,
)
from .storage import AbsorptionPathError
from .traversal import GraphDefectError, ImpactSet

router = APIRouter(tags=["absorption"])

_PROJECT = "/api/v1/projects/{project_id}"
_TICKET = _PROJECT + "/changes/{drop_id}/{requirement_id}"

# `absorb` and `impact` sit on their OWN first-level segments rather than
# under /changes/, so no two routes in this module share a shape. Nesting
# /changes/impact/{requirement_id} beside /changes/{drop_id}/{requirement_id}
# would make correctness depend on declaration order — the literal route
# shadowed by the two-variable one, silently, with `drop_id="impact"`.

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})

_AUTHORS = ("project_editor", "project_owner")
_OWNERS = ("project_owner",)


def get_absorption_service(request: Request) -> AbsorptionService:
    """FastAPI dependency resolved from app.state.absorption_service."""
    service = getattr(request.app.state, "absorption_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="absorption service not configured",
        )
    return service  # type: ignore[no-any-return]


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_role(x_user_roles: str | None, required: tuple[str, ...]) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    roles -= _CONTENT_BLIND_GLOBAL_ROLES
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


def _refused(exc: Exception) -> HTTPException:
    # 409: well-formed request, entitled caller, and the PROCESS refuses.
    # The message names the rule so the workbench can explain it.
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _missing(exc: TicketNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _bad_path(exc: AbsorptionPathError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


def _invalid(exc: ValidationError) -> HTTPException:
    # 422, not 500. A model invariant refused the request — a disposition
    # with no justification, say (R-310-150). FastAPI validates the BODY
    # before the handler, but these invariants fire when the service
    # CONSTRUCTS the record, which is after that, so the exception escapes
    # unless it is caught here.
    #
    # `str(exc)` rather than `exc.errors()`, matching `process/router.py`:
    # the structured form embeds the offending input, which here contains a
    # datetime and is not JSON-serialisable.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


# ---------------------------------------------------------------------------
# Absorbing a re-supplied drop — the ONLY way a ticket comes into being
# ---------------------------------------------------------------------------


@router.post(
    _PROJECT + "/absorb/{drop_id}",
    response_model=AbsorbResponse,
    status_code=status.HTTP_201_CREATED,
)
async def absorb_drop(
    project_id: str,
    drop_id: str,
    previous: list[RequirementSnapshot],
    current: list[RequirementSnapshot],
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AbsorptionService = Depends(get_absorption_service),
) -> AbsorbResponse:
    """Diff a re-supplied drop and open one ticket per change (`R-310-140`)."""
    _require_role(x_user_roles, _OWNERS)
    try:
        difference, tickets = await service.absorb(
            project_id, drop_id, previous=previous, current=current
        )
    except DiffError as exc:
        raise _refused(exc) from exc
    except GraphDefectError as exc:
        raise _refused(exc) from exc
    except AbsorptionPathError as exc:
        raise _bad_path(exc) from exc
    return AbsorbResponse(
        drop_id=drop_id,
        opened=tuple(ticket.ticket_id for ticket in tickets),
        unchanged_count=len(difference.unchanged_ids),
        reformatted_ids=difference.reformatted_ids,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(_PROJECT + "/changes", response_model=TicketListResponse)
async def list_changes(
    project_id: str,
    only_open: bool = False,
    actor: str = Depends(_require_actor),
    service: AbsorptionService = Depends(get_absorption_service),
) -> TicketListResponse:
    """List a project's change tickets."""
    try:
        tickets = await service.list_tickets(project_id, only_open=only_open)
    except AbsorptionPathError as exc:
        raise _bad_path(exc) from exc
    return TicketListResponse(tickets=tickets, count=len(tickets))


@router.get(_TICKET, response_model=ChangeTicket)
async def get_change(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    actor: str = Depends(_require_actor),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ChangeTicket:
    """Return one change ticket with its impact set."""
    try:
        return await service.get_ticket(project_id, drop_id, requirement_id)
    except TicketNotFoundError as exc:
        raise _missing(exc) from exc
    except AbsorptionPathError as exc:
        raise _bad_path(exc) from exc


@router.get(_PROJECT + "/impact/{requirement_id}", response_model=ImpactSet)
async def preview_impact(
    project_id: str,
    requirement_id: str,
    actor: str = Depends(_require_actor),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ImpactSet:
    """Return the impact set of a requirement without opening a ticket.

    The blast radius of a prospective change, so a reviewer can see what a
    negotiation would cost before agreeing to it.
    """
    try:
        return await service.impact_of(project_id, requirement_id)
    except GraphDefectError as exc:
        raise _refused(exc) from exc


@router.get(_TICKET + "/closure", response_model=ClosureReport)
async def closure_report(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    actor: str = Depends(_require_actor),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ClosureReport:
    """Evaluate the closure gate without closing (`R-310-146`)."""
    try:
        return await service.closure_report(project_id, drop_id, requirement_id)
    except TicketNotFoundError as exc:
        raise _missing(exc) from exc
    except AbsorptionPathError as exc:
        raise _bad_path(exc) from exc


# ---------------------------------------------------------------------------
# Qualification — R-310-148
# ---------------------------------------------------------------------------


@router.post(_TICKET + "/qualify", response_model=ChangeTicket)
async def qualify_node(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    body: QualifyRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ChangeTicket:
    """Propose a qualification for one impacted node."""
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.qualify(
            project_id,
            drop_id,
            requirement_id,
            node_id=body.node_id,
            qualification=body.qualification,
            justification=body.justification,
            actor=actor,
        )
    except TicketNotFoundError as exc:
        raise _missing(exc) from exc
    except AbsorptionRefusedError as exc:
        raise _refused(exc) from exc
    except ValidationError as exc:
        raise _invalid(exc) from exc


@router.post(_TICKET + "/qualify/accept", response_model=ChangeTicket)
async def accept_qualification(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    body: AcceptQualificationRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ChangeTicket:
    """Pass the human gate on one node's qualification (`R-310-148`)."""
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.accept_qualification(
            project_id, drop_id, requirement_id, node_id=body.node_id, actor=actor
        )
    except TicketNotFoundError as exc:
        raise _missing(exc) from exc
    except AbsorptionRefusedError as exc:
        raise _refused(exc) from exc


# ---------------------------------------------------------------------------
# Disposition and closure — R-310-146 / R-310-150
# ---------------------------------------------------------------------------


@router.post(_TICKET + "/disposition", response_model=ChangeTicket)
async def disposition_node(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    body: DispositionRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ChangeTicket:
    """Record how one impacted node was disposed of (`R-310-150`)."""
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.disposition(
            project_id,
            drop_id,
            requirement_id,
            node_id=body.node_id,
            kind=body.kind,
            actor=actor,
            justification=body.justification,
            object_version=body.object_version,
        )
    except TicketNotFoundError as exc:
        raise _missing(exc) from exc
    except AbsorptionRefusedError as exc:
        raise _refused(exc) from exc
    except ValidationError as exc:
        raise _invalid(exc) from exc


@router.post(_TICKET + "/close", response_model=ChangeTicket)
async def close_change(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AbsorptionService = Depends(get_absorption_service),
) -> ChangeTicket:
    """Close a ticket if and only if `R-310-146` permits it.

    Not a status write — a gate. The 409 body carries the full report, so a
    caller learns every outstanding precondition rather than the first.
    """
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.close(project_id, drop_id, requirement_id, actor=actor)
    except TicketNotFoundError as exc:
        raise _missing(exc) from exc
    except ClosureRefusedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.report.model_dump(mode="json"),
        ) from exc
