# =============================================================================
# File: router.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/router.py
# Description: FastAPI APIRouter for the global LLM quota policy (Lot 3),
#              platform_manager only (E-100-002 v3 platform operator). Identity
#              arrives via Traefik forward-auth headers (X-User-Id /
#              X-User-Roles), same pattern as the registry surface. Exposes
#              GET/PUT of the single global policy and a per-tenant status
#              read for the oversight HMI. Mounted by the c8_admin app factory.
#              v3 (E-100-002 v7): adds GET /admin/v1/quota/consumption/projects
#              — per-project cost across day..year, an OPERATOR surface
#              (platform_manager cross-tenant; admin/tenant_admin own tenant).
# @relation implements:R-800-144
# @relation implements:R-800-145
# @relation implements:R-800-146
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ay_platform_core.c8_llm.quota.models import (
    ConsumptionReport,
    ProjectConsumptionReport,
    QuotaPolicy,
    QuotaPolicyUpdate,
    QuotaStatus,
    RequestCostBreakdown,
    UserConsumptionReport,
)
from ay_platform_core.c8_llm.quota.service import QuotaService

router = APIRouter(tags=["llm-quota"])

_QUOTA_ROLES: tuple[str, ...] = ("platform_manager",)
# Project cost reporting is an OPERATOR surface: platform_manager (cross-tenant)
# OR the tenant operator admin/tenant_admin (own tenant only, E-100-002 v7).
_QUOTA_OPERATOR_ROLES: tuple[str, ...] = ("platform_manager", "admin", "tenant_admin")


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


def _operator_tenant_scope(
    x_user_roles: str | None, x_tenant_id: str | None, query_tenant_id: str | None
) -> str | None:
    """The tenant a project-cost report is confined to. platform_manager is
    cross-tenant (uses the optional `?tenant_id=` filter, else all); a tenant
    operator is FORCED to its own `X-Tenant-Id` (401 if absent)."""
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if "platform_manager" in roles:
        return query_tenant_id
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header missing for a tenant-scoped operator",
        )
    return x_tenant_id


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
    """Replace the global policy window set. platform_manager only."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.set_policy(body.windows)


@router.get("/admin/v1/quota/status", response_model=QuotaStatus)
async def get_quota_status(
    tenant_id: str,
    project_id: str | None = None,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> QuotaStatus:
    """Evaluate a tenant against the global policy (usage / limit / state per
    window). Read-only oversight for the operator HMI. platform_manager only.

    When `project_id` is supplied, the evaluation additionally resolves the
    PROJECT level, so each window's `levels` carries that project's
    consumption (cost / tokens) — the project-governance consumption view
    (E-100-002 v4). Consumption is a numeric aggregate over the project's
    `llm_calls`; it exposes no project CONTENT, so it stays within the
    operator's content-blind mandate."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.evaluate(tenant_id, project_id=project_id)


@router.get("/admin/v1/quota/consumption", response_model=ConsumptionReport)
async def get_consumption(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> ConsumptionReport:
    """Per-tenant LLM consumption across the reporting windows session / week /
    month / quarter / semester / year (R-800-144). Read-only operator oversight
    (platform_manager). Reporting only — the ENFORCED quota policy is
    untouched. Amounts are in the platform currency."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.consumption_report()


@router.get(
    "/admin/v1/quota/consumption/projects",
    response_model=ProjectConsumptionReport,
)
async def get_project_consumption(
    tenant_id: str | None = None,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> ProjectConsumptionReport:
    """Per-project LLM cost across day / week / month / quarter / semester /
    year (E-100-002 v7 project cost dashboards). platform_manager sees ALL
    tenants (optional `?tenant_id=` filter); admin / tenant_admin is confined to
    its own tenant (`X-Tenant-Id`). Reporting only."""
    _require_role(x_user_roles, _QUOTA_OPERATOR_ROLES)
    scope = _operator_tenant_scope(x_user_roles, x_tenant_id, tenant_id)
    return await service.project_consumption_report(tenant_id=scope)


@router.get(
    "/admin/v1/quota/consumption/tenants", response_model=ConsumptionReport
)
async def get_tenant_consumption_days(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> ConsumptionReport:
    """Per-tenant LLM cost across day..year (E-100-002 v7 tenant cost
    dashboards). platform_manager only (tenants are platform-wide)."""
    _require_role(x_user_roles, _QUOTA_ROLES)
    return await service.tenant_consumption_report_days()


@router.get(
    "/admin/v1/quota/consumption/users", response_model=UserConsumptionReport
)
async def get_user_consumption(
    tenant_id: str | None = None,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> UserConsumptionReport:
    """Per-user LLM cost across day..year (E-100-002 v7 user cost dashboards).
    platform_manager sees ALL tenants (optional `?tenant_id=`); admin /
    tenant_admin is confined to its own tenant (`X-Tenant-Id`)."""
    _require_role(x_user_roles, _QUOTA_OPERATOR_ROLES)
    scope = _operator_tenant_scope(x_user_roles, x_tenant_id, tenant_id)
    return await service.user_consumption_report(tenant_id=scope)


@router.get(
    "/admin/v1/quota/requests/{correlation}/breakdown",
    response_model=RequestCostBreakdown,
)
async def get_request_breakdown(
    correlation: str,
    by: str = "run",
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    service: QuotaService = Depends(get_quota_service),
) -> RequestCostBreakdown:
    """The model-mix cost/token breakdown of ONE request (`by=run` → run_id,
    `by=turn` → turn_id): total + per-model split with % (R-800-146). Operator
    surface: a tenant operator is confined to its own `X-Tenant-Id`;
    platform_manager is cross-tenant (optional `?tenant_id=`)."""
    _require_role(x_user_roles, _QUOTA_OPERATOR_ROLES)
    if by not in ("run", "turn"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="by must be 'run' or 'turn'",
        )
    scope = _operator_tenant_scope(x_user_roles, x_tenant_id, None)
    return await service.request_breakdown(correlation, by, tenant_id=scope)


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
