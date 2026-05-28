# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/router.py
# Description: FastAPI APIRouter for C6 per 700-SPEC §8. Forward-auth headers
#              (X-User-Id, X-User-Roles, X-Tenant-Id) are injected by C1/C2
#              and consumed here for RBAC gating.
#
# @relation implements:R-700-010
# @relation implements:R-700-012
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from ay_platform_core.c6_validation.models import (
    DomainList,
    Finding,
    FindingPage,
    PluginDescriptor,
    RunTriggerRequest,
    RunTriggerResponse,
    ValidationRun,
)
from ay_platform_core.c6_validation.service import ValidationService, get_service

router = APIRouter(tags=["validation"])

# ---------------------------------------------------------------------------
# RBAC helpers — identical pattern to C3/C4/C5/C7
# ---------------------------------------------------------------------------


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_role(
    x_user_roles: str | None,
    required: tuple[str, ...],
) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


# ---------------------------------------------------------------------------
# Plugin / domain discovery
# ---------------------------------------------------------------------------


@router.get("/api/v1/validation/plugins", response_model=list[PluginDescriptor])
async def list_plugins(
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> list[PluginDescriptor]:
    return service.list_plugins()


@router.get("/api/v1/validation/domains", response_model=DomainList)
async def list_domains(
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> DomainList:
    return DomainList(domains=service.list_domains())


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/validation/runs",
    response_model=RunTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_run(
    payload: RunTriggerRequest,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ValidationService = Depends(get_service),
) -> RunTriggerResponse:
    # Triggering a validation run is a project-level action — allowed for
    # editors, owners, and admins.
    _require_role(x_user_roles, required=("project_editor", "project_owner", "admin"))
    return await service.trigger_run(
        payload,
        requirements=payload.requirements,
        artifacts=payload.artifacts,
    )


@router.get(
    "/api/v1/validation/runs/{run_id}",
    response_model=ValidationRun,
)
async def get_run(
    run_id: str,
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> ValidationRun:
    return await service.get_run(run_id)


@router.get(
    "/api/v1/validation/runs/{run_id}/findings",
    response_model=FindingPage,
)
async def list_findings(
    run_id: str,
    limit: int = 100,
    offset: int = 0,
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> FindingPage:
    if limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be in [1, 1000]",
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0"
        )
    return await service.list_findings(run_id, limit=limit, offset=offset)


@router.get(
    "/api/v1/validation/findings/{finding_id}",
    response_model=Finding,
)
async def get_finding(
    finding_id: str,
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> Finding:
    return await service.get_finding(finding_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/validation/health",
    response_model=None,
)
async def health(
    service: ValidationService = Depends(get_service),
) -> dict[str, str]:
    _ = service
    return {"status": "ok"}
