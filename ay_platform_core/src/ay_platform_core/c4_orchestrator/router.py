# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/router.py
# Description: FastAPI APIRouter for C4 per 200-SPEC §6.1. Identity is
#              consumed from the Traefik forward-auth headers propagated
#              by C2 `/auth/verify` (X-User-Id, X-User-Roles, X-Tenant-Id).
#
#              v2 (2026-05-20) : Tranche B endpoints — `GET /runs/{id}/
#              trace?before=<ts>&limit=N` (R-200-201) and `POST /runs/
#              {id}/steer` (R-200-202). Both reuse the actor-only auth
#              guard ; RBAC inside the methods aligns with `/feedback`
#              (project_owner / project_editor / admin), tenant_manager
#              rejected on content endpoints (E-100-002 v2).
#
# @relation implements:R-200-002
# @relation implements:R-200-201
# @relation implements:R-200-202
# @relation implements:E-200-005
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from ay_platform_core.c4_orchestrator.models import (
    RunCreate,
    RunFeedback,
    RunPublic,
    RunResume,
    RunSteer,
    TraceEvent,
)
from ay_platform_core.c4_orchestrator.service import (
    OrchestratorService,
    get_service,
)

router = APIRouter(tags=["orchestrator"])


# ---------------------------------------------------------------------------
# Identity + RBAC helpers (same pattern as C3/C5)
# ---------------------------------------------------------------------------


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
            detail="X-Tenant-Id header missing",
        )
    return x_tenant_id


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
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/orchestrator/runs",
    response_model=RunPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    payload: RunCreate,
    user_id: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: OrchestratorService = Depends(get_service),
) -> RunPublic:
    return await service.start_run(payload, tenant_id=tenant_id, user_id=user_id)


@router.get(
    "/api/v1/orchestrator/runs/{run_id}",
    response_model=RunPublic,
)
async def get_run(
    run_id: str,
    _user: str = Depends(_require_actor),
    service: OrchestratorService = Depends(get_service),
) -> RunPublic:
    return await service.get_run(run_id)


@router.post(
    "/api/v1/orchestrator/runs/{run_id}/feedback",
    response_model=RunPublic,
)
async def submit_feedback(
    run_id: str,
    payload: RunFeedback,
    _user: str = Depends(_require_actor),
    service: OrchestratorService = Depends(get_service),
) -> RunPublic:
    return await service.handle_feedback(run_id, payload)


@router.post(
    "/api/v1/orchestrator/runs/{run_id}/resume",
    response_model=RunPublic,
)
async def resume_run(
    run_id: str,
    payload: RunResume,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: OrchestratorService = Depends(get_service),
) -> RunPublic:
    # Admin endpoint — only `admin` global role may resume.
    _require_role(x_user_roles, required=("admin",))
    return await service.resume_run(run_id, payload)


@router.get(
    "/api/v1/orchestrator/runs/{run_id}/trace",
    response_model=list[TraceEvent],
)
async def read_trace(
    run_id: str,
    before: str | None = Query(
        default=None,
        description=(
            "ISO-8601 timestamp ; returns events strictly older than this. "
            "Omit for the most-recent slice."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=200),
    _user: str = Depends(_require_actor),
    service: OrchestratorService = Depends(get_service),
) -> list[TraceEvent]:
    """Paginated back-in-time read of a run's TraceEvent ledger
    (R-200-201). Newest-first, capped at `limit` (≤ 200)."""
    return await service.get_run_trace(run_id, before_iso=before, limit=limit)


@router.post(
    "/api/v1/orchestrator/runs/{run_id}/steer",
    response_model=RunPublic,
)
async def steer_run(
    run_id: str,
    payload: RunSteer,
    _user: str = Depends(_require_actor),
    service: OrchestratorService = Depends(get_service),
) -> RunPublic:
    """Queue a steering hint for consumption at the next phase or
    sub-agent-tour boundary (R-200-202..203). Returns 409 when the run
    is not RUNNING. RBAC follows `/feedback`."""
    return await service.steer_run(run_id, payload)
