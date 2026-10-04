# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/router.py
# Description: REST surface for negotiated piloting — 310-SPEC §4.9.
#
#              THE ROLE SPLIT IS THE REQUIREMENT. `R-310-170` says a HUMAN
#              ratifies; so `/ratify` and `/amend` are `project_owner` and
#              nothing else, while proposing and executing are authoring
#              work an agent does under `project_editor`. An editor who
#              could ratify would be able to approve its own plan, which
#              dissolves the gate the requirement exists to create.
#
#              409 IS THE REFUSAL, AND IT NAMES THE RULE. An unratified
#              plan, a version mismatch, a partition that loses a unit — all
#              well-formed requests from entitled callers that the PROCESS
#              refuses. The workbench needs the message to explain why, not
#              a bare status.
#
#              EVERY ROUTE HAS A DISTINCT SHAPE. Checked by enumerating
#              them, after `/changes/impact/{id}` was silently shadowed by
#              `/changes/{drop}/{req}` in increment 5. The literal segment
#              comes last here (`/plans/{plan_id}/ratify`), so no
#              two-variable path can swallow it.
#
# @relation implements:R-310-170
# @relation implements:R-310-171
# @relation implements:R-310-172
# @relation implements:R-310-173
# @relation implements:R-310-174
# @relation implements:R-310-175
# @relation implements:R-310-176
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from .budget import Consumption
from .models import (
    AmendRequest,
    ConsumptionRequest,
    PlanListResponse,
    ProposeRequest,
    RatifyRequest,
    ReportRequest,
    TreatmentPlan,
    TreatmentReport,
)
from .service import (
    ExecutionService,
    NotRatifiedError,
    PlanNotFoundError,
    PlanRefusedError,
)
from .storage import ExecutionPathError

router = APIRouter(tags=["execution"])

_PLANS = "/api/v1/projects/{project_id}/plans"
_PLAN = _PLANS + "/{plan_id}"
_STEP = _PLAN + "/steps/{step_id}"

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})

_AUTHORS = ("project_editor", "project_owner")
_OWNERS = ("project_owner",)


def get_execution_service(request: Request) -> ExecutionService:
    """FastAPI dependency resolved from app.state.execution_service."""
    service = getattr(request.app.state, "execution_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="execution service not configured",
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
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _missing(exc: PlanNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    # 422: the stored entity or the derived step, not the request body, is
    # what failed validation — a partition producing an empty step, say.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


# ---------------------------------------------------------------------------
# Proposal and ratification — R-310-170 / R-310-171
# ---------------------------------------------------------------------------


@router.post(
    _PLAN + "/propose",
    response_model=TreatmentPlan,
    status_code=status.HTTP_201_CREATED,
)
async def propose_plan(
    project_id: str,
    plan_id: str,
    body: ProposeRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Propose a priced, decomposed plan over a batch (`R-310-171`)."""
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.propose(
            project_id,
            plan_id,
            batch=body.batch,
            units=body.units,
            actor=actor,
        )
    except PlanRefusedError as exc:
        raise _refused(exc) from exc
    except ExecutionPathError as exc:
        raise _unprocessable(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc


@router.post(_PLAN + "/ratify", response_model=TreatmentPlan)
async def ratify_plan(
    project_id: str,
    plan_id: str,
    body: RatifyRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Ratify the plan as it stands — owner only (`R-310-170`).

    An editor able to ratify could approve its own plan, which dissolves
    the gate.
    """
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.ratify(
            project_id, plan_id, plan_version=body.plan_version, actor=actor
        )
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc


@router.post(_PLAN + "/amend", response_model=TreatmentPlan)
async def amend_plan(
    project_id: str,
    plan_id: str,
    body: AmendRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Re-split one step mid-flight (`R-310-173`).

    Owner-gated: an amendment changes the terms of work already approved,
    so it needs the same authority that approved them. The amended plan is
    unratified until approved again.
    """
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.amend(
            project_id,
            plan_id,
            step_id=body.step_id,
            partitions=body.partitions,
            rationale=body.rationale,
            actor=actor,
        )
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(_PLANS, response_model=PlanListResponse)
async def list_plans(
    project_id: str,
    awaiting_ratification: bool = False,
    active_only: bool = False,
    actor: str = Depends(_require_actor),
    service: ExecutionService = Depends(get_execution_service),
) -> PlanListResponse:
    """List a project's treatment plans."""
    try:
        plans = await service.list_plans(
            project_id,
            awaiting_ratification=awaiting_ratification,
            active_only=active_only,
        )
    except ExecutionPathError as exc:
        raise _unprocessable(exc) from exc
    return PlanListResponse(plans=plans, count=len(plans))


@router.get(_PLAN, response_model=TreatmentPlan)
async def get_plan(
    project_id: str,
    plan_id: str,
    actor: str = Depends(_require_actor),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Return a plan's current version."""
    try:
        return await service.get_plan(project_id, plan_id)
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except ExecutionPathError as exc:
        raise _unprocessable(exc) from exc


@router.get(_PLAN + "/versions/{version}", response_model=TreatmentPlan)
async def get_plan_version(
    project_id: str,
    plan_id: str,
    version: int,
    actor: str = Depends(_require_actor),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Return one historical version — the terms actually approved.

    This is what makes a ratification auditable after an amendment
    (`R-310-175`).
    """
    try:
        return await service.get_plan_version(project_id, plan_id, version)
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except ExecutionPathError as exc:
        raise _unprocessable(exc) from exc


# ---------------------------------------------------------------------------
# Execution — R-310-170 / R-310-174
# ---------------------------------------------------------------------------


@router.post(_STEP + "/begin", response_model=TreatmentPlan)
async def begin_step(
    project_id: str,
    plan_id: str,
    step_id: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Begin a step, refused on an unratified plan (`R-310-170`).

    423 Locked rather than 409 for the unratified case: the request is
    blocked by a missing human decision, not by a conflict the caller can
    resolve — the same distinction as a foreign edit lease (DV-14).
    """
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.begin_step(project_id, plan_id, step_id=step_id)
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except NotRatifiedError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED, detail=str(exc)
        ) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc


@router.post(_STEP + "/consumption", response_model=TreatmentPlan)
async def record_consumption(
    project_id: str,
    plan_id: str,
    step_id: str,
    body: ConsumptionRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Report consumption; the step suspends if it is over (`R-310-174`).

    Returns 200 either way, with the plan as the body: a suspension is an
    outcome of the report, not a rejection of it. The caller learns it
    suspended by reading the step's state and its `suspended_reason`.
    """
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.record_consumption(
            project_id,
            plan_id,
            step_id=step_id,
            consumed=Consumption(
                tokens=body.tokens,
                objects=body.objects,
                review_items=body.review_items,
            ),
        )
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc


@router.post(_STEP + "/complete", response_model=TreatmentPlan)
async def complete_step(
    project_id: str,
    plan_id: str,
    step_id: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Mark a step completed."""
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.complete_step(project_id, plan_id, step_id=step_id)
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc


@router.post(_STEP + "/fail", response_model=TreatmentPlan)
async def fail_step(
    project_id: str,
    plan_id: str,
    step_id: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentPlan:
    """Mark a step failed — an error, distinct from an overrun."""
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.fail_step(project_id, plan_id, step_id=step_id)
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc


# ---------------------------------------------------------------------------
# The treatment report — R-310-176
# ---------------------------------------------------------------------------


@router.post(
    _PLAN + "/report",
    response_model=TreatmentReport,
    status_code=status.HTTP_201_CREATED,
)
async def produce_report(
    project_id: str,
    plan_id: str,
    body: ReportRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentReport:
    """Produce the report of a completed plan (`R-310-176`)."""
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.produce_report(
            project_id,
            plan_id,
            containers_modified=body.containers_modified,
            objects_created=body.objects_created,
            review_outcomes=body.review_outcomes,
            coverage_before=body.coverage_before,
            coverage_after=body.coverage_after,
            remaining_gaps=body.remaining_gaps,
            returns_awaiting_arbitration=body.returns_awaiting_arbitration,
        )
    except PlanNotFoundError as exc:
        raise _missing(exc) from exc
    except PlanRefusedError as exc:
        raise _refused(exc) from exc


@router.get(_PLAN + "/report", response_model=TreatmentReport)
async def get_report(
    project_id: str,
    plan_id: str,
    actor: str = Depends(_require_actor),
    service: ExecutionService = Depends(get_execution_service),
) -> TreatmentReport:
    """Return a plan's treatment report."""
    try:
        report = await service.get_report(project_id, plan_id)
    except ExecutionPathError as exc:
        raise _unprocessable(exc) from exc
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no treatment report for {plan_id!r} yet",
        )
    return report
