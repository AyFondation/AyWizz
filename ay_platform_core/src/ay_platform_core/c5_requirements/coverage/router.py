# =============================================================================
# File: router.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/coverage/router.py
# Description: REST surface for the traceability graph — 310-SPEC §4.4 / §4.7.
#
#              Role gates:
#                - proposing an allocation and recording coverage are
#                  authoring work: `project_editor` or `project_owner`;
#                - RETURNING an allocation and declaring a requirement
#                  out-of-project are owner decisions. `R-310-068` reserves
#                  the return to "the owner of the container", but per-
#                  container ownership is NOT modelled anywhere in the
#                  platform (see DV-18). `project_owner` is the conservative
#                  stand-in: it never grants a right the spec withholds, it
#                  only withholds one it might eventually grant.
#
#              Reads are merely authenticated: the coverage matrix is what
#              tells a team what it owes, and hiding it from the people doing
#              the work would defeat its purpose.
#
#              `POST .../audit/unallocated` is a POST despite being a read:
#              R-310-065 asks the question of a CANDIDATE set, which at the
#              volume of R-310-300 does not fit in a query string.
#
# @relation implements:R-310-065
# @relation implements:R-310-066
# @relation implements:R-310-068
# @relation implements:R-310-069
# @relation implements:R-310-120
# @relation implements:R-310-121
# @relation implements:R-310-145
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from ay_platform_core.c2_auth.forward_auth import (
    require_project_content_role,
)

from .models import (
    AcceptRequest,
    AllocateRequest,
    Allocation,
    AuditRequest,
    ContainerCoverage,
    CoverageLink,
    CoverRequest,
    OutOfProjectVerdict,
    RequirementCoverage,
    ReturnRequest,
    ReturnResponse,
    SpeculativeListResponse,
    SuspectLinkListResponse,
    UnallocatedResponse,
    VerdictRequest,
)
from .service import (
    AllocationRefusedError,
    CoverageRefusedError,
    CoverageService,
)

# Defence in depth (E-100-002 v8). Every route below whose path carries a
# `/projects/<id>/` segment requires a project grant on THAT project, in
# addition to whatever stricter gate the individual route declares. The
# gateway already refuses such a request, so this is the SECOND line: a
# component reached directly — a port-forward, a mesh topology, a
# mistaken `expose:` — meets no gate at all without it. Attached to the
# ROUTER so a route nobody has written yet inherits it; the dependency
# self-limits to project paths, leaving tenant-level and /health routes
# in mixed routers untouched. See `c2_auth.forward_auth`.
router = APIRouter(
    tags=["coverage"],
    dependencies=[Depends(require_project_content_role)],
)

_BASE = "/api/v1/projects/{project_id}/coverage"

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})

_AUTHORS = ("project_editor", "project_owner")
_OWNERS = ("project_owner",)




# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_coverage_service(request: Request) -> CoverageService:
    """FastAPI dependency resolved from app.state.coverage_service."""
    service = getattr(request.app.state, "coverage_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="coverage service not configured",
        )
    return service  # type: ignore[no-any-return]


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header missing (forward-auth not applied)",
        )
    return x_tenant_id


def _require_role(x_user_roles: str | None, required: tuple[str, ...]) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    roles -= _CONTENT_BLIND_GLOBAL_ROLES
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


def _refused(exc: Exception) -> HTTPException:
    # 409, not 400: the body was well-formed and the caller entitled to
    # write; the PROCESS refuses this particular statement. The message
    # names the rule, so the UI can explain rather than just fail.
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _invalid(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


@router.post(
    f"{_BASE}/allocations",
    response_model=Allocation,
    status_code=status.HTTP_201_CREATED,
)
async def allocate(
    project_id: str,
    payload: AllocateRequest,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: CoverageService = Depends(get_coverage_service),
) -> Allocation:
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.allocate(
            tenant_id, project_id, payload.cycle_id,
            requirement_id=payload.requirement_id,
            container=payload.container,
            scope_id=payload.scope_id,
            justification=payload.justification,
            actor=actor,
            criticality=payload.criticality,
            inherited_criticality=payload.inherited_criticality,
            decomposition_rationale=payload.decomposition_rationale,
            override_exclusion=payload.override_exclusion,
        )
    except AllocationRefusedError as exc:
        raise _refused(exc) from exc
    except ValidationError as exc:
        raise _invalid(exc) from exc


@router.post(
    f"{_BASE}/allocations/{{requirement_id}}/containers/{{container}}/accept",
    response_model=Allocation,
)
async def accept_allocation(
    project_id: str,
    requirement_id: str,
    container: str,
    payload: AcceptRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: CoverageService = Depends(get_coverage_service),
) -> Allocation:
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.accept_allocation(
            project_id, requirement_id, container, actor=actor, auto=payload.auto
        )
    except AllocationRefusedError as exc:
        raise _refused(exc) from exc


@router.post(
    f"{_BASE}/allocations/{{requirement_id}}/containers/{{container}}/return",
    response_model=ReturnResponse,
)
async def return_allocation(
    project_id: str,
    requirement_id: str,
    container: str,
    payload: ReturnRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: CoverageService = Depends(get_coverage_service),
) -> ReturnResponse:
    # R-310-068 reserves this to the container owner; see the module header
    # for why project_owner is the conservative stand-in.
    _require_role(x_user_roles, _OWNERS)
    try:
        outcome = await service.return_allocation(
            project_id, requirement_id, container,
            reason=payload.reason, detail=payload.detail, actor=actor,
        )
    except ValidationError as exc:
        raise _invalid(exc) from exc
    return ReturnResponse(
        ordinal=outcome.ordinal,
        escalated=outcome.escalated,
        next_exclusions=list(outcome.next_exclusions),
    )


@router.post(
    f"{_BASE}/verdicts",
    response_model=OutOfProjectVerdict,
    status_code=status.HTTP_201_CREATED,
)
async def record_verdict(
    project_id: str,
    payload: VerdictRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: CoverageService = Depends(get_coverage_service),
) -> OutOfProjectVerdict:
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.record_out_of_project(
            project_id, payload.requirement_id,
            justification=payload.justification, actor=actor,
        )
    except ValidationError as exc:
        raise _invalid(exc) from exc


# ---------------------------------------------------------------------------
# Coverage links
# ---------------------------------------------------------------------------


@router.post(
    f"{_BASE}/links",
    response_model=CoverageLink,
    status_code=status.HTTP_201_CREATED,
)
async def cover(
    project_id: str,
    payload: CoverRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: CoverageService = Depends(get_coverage_service),
) -> CoverageLink:
    _require_role(x_user_roles, _AUTHORS)
    try:
        return await service.cover(
            project_id,
            object_id=payload.object_id,
            container=payload.container,
            target_id=payload.target_id,
            actor=actor,
            strength=payload.strength,
        )
    except CoverageRefusedError as exc:
        raise _refused(exc) from exc
    except ValidationError as exc:
        raise _invalid(exc) from exc


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


@router.get(
    f"{_BASE}/requirements/{{requirement_id}}", response_model=RequirementCoverage
)
async def requirement_coverage(
    project_id: str,
    requirement_id: str,
    _actor: str = Depends(_require_actor),
    service: CoverageService = Depends(get_coverage_service),
) -> RequirementCoverage:
    return await service.requirement_coverage(project_id, requirement_id)


@router.get(f"{_BASE}/containers/{{container}}", response_model=ContainerCoverage)
async def container_coverage(
    project_id: str,
    container: str,
    _actor: str = Depends(_require_actor),
    service: CoverageService = Depends(get_coverage_service),
) -> ContainerCoverage:
    return await service.container_coverage(project_id, container)


@router.get(f"{_BASE}/suspect", response_model=SuspectLinkListResponse)
async def suspect_links(
    project_id: str,
    _actor: str = Depends(_require_actor),
    service: CoverageService = Depends(get_coverage_service),
) -> SuspectLinkListResponse:
    return SuspectLinkListResponse(links=await service.suspect_links(project_id))


@router.get(f"{_BASE}/speculative", response_model=SpeculativeListResponse)
async def speculative_objects(
    project_id: str,
    container: str | None = None,
    _actor: str = Depends(_require_actor),
    service: CoverageService = Depends(get_coverage_service),
) -> SpeculativeListResponse:
    """Return every object built on an upstream nobody has accepted yet.

    `R-310-177` v2 requires such an object to be MARKED speculative. The
    marking is computed on every call rather than stored, so it cannot
    claim an object is speculative after its last unaccepted upstream was
    accepted — no write happens at that moment to clear a flag.

    `stale_count` is surfaced separately because those are the objects a
    reviewer must look at NOW: their upstream changed before acceptance, so
    they answer a version of something that no longer exists.
    """
    markings = await service.speculative_objects(project_id, container)
    return SpeculativeListResponse(
        markings=markings,
        count=len(markings),
        stale_count=sum(1 for marking in markings if marking.must_become_stale),
    )


@router.post(f"{_BASE}/audit/unallocated", response_model=UnallocatedResponse)
async def audit_unallocated(
    project_id: str,
    payload: AuditRequest,
    _actor: str = Depends(_require_actor),
    service: CoverageService = Depends(get_coverage_service),
) -> UnallocatedResponse:
    return UnallocatedResponse(
        unallocated=await service.audit_unallocated(project_id, payload.candidates)
    )
