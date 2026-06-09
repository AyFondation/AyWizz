# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/router.py
# Description: FastAPI APIRouter for the global LLM quota policy (Lot 3),
#              tenant_manager only (E-100-002 v3 platform operator). Identity
#              arrives via Traefik forward-auth headers (X-User-Id /
#              X-User-Roles), same pattern as the registry surface. Exposes
#              GET/PUT of the single global policy and a per-tenant status
#              read for the oversight HMI. Mounted by the c8_admin app factory.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ay_platform_core.c8_llm.quota.models import (
    QuotaPolicy,
    QuotaPolicyUpdate,
    QuotaStatus,
)
from ay_platform_core.c8_llm.quota.service import QuotaService

router = APIRouter(tags=["llm-quota"])

_QUOTA_ROLES: tuple[str, ...] = ("tenant_manager",)


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_role(x_user_roles: str | None, required: tuple[str, ...]) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


def get_quota_service(request: Request) -> QuotaService:
    """Resolve the quota service from app state (set by the app factory)."""
    return request.app.state.quota_service  # type: ignore[no-any-return]


@router.get("/admin/v1/quota/policy", response_model=QuotaPolicy)
async def get_quota_policy(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> QuotaPolicy:
    """Return the single global quota policy (default windows if never set)."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.get_policy()


@router.put("/admin/v1/quota/policy", response_model=QuotaPolicy)
async def put_quota_policy(
    body: QuotaPolicyUpdate,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> QuotaPolicy:
    """Replace the global policy window set. tenant_manager only."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.set_policy(body.windows)


@router.get("/admin/v1/quota/status", response_model=QuotaStatus)
async def get_quota_status(
    tenant_id: str,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> QuotaStatus:
    """Evaluate a tenant against the global policy (usage / limit / state per
    window). Read-only oversight for the operator HMI. tenant_manager only."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.evaluate(tenant_id)


@router.get("/api/v1/quota/me", response_model=QuotaStatus)
async def get_my_quota(
    _user: str = Depends(_require_actor),
    x_tenant_id: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> QuotaStatus:
    """The CALLER's own tenant quota status — usage / limit / state per window.
    Available to ANY authenticated user (no role gate): everyone in a tenant
    shares its global quota and may watch it live. The tenant is taken from the
    forward-auth `X-Tenant-Id` header, never a client-supplied parameter (no
    cross-tenant peeking)."""
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id header missing (forward-auth not applied)",
        )
    # Include the caller as the `user` subject so per-user caps surface in their
    # own pill (the tenant + global levels show alongside).
    return await service.evaluate(x_tenant_id, user_id=_user)
