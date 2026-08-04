# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/storage/router.py
# Description: FastAPI APIRouter for the per-project storage dashboards
#              (E-100-002 v7), mounted by the c8_admin app. Forward-auth
#              identity (X-User-Id / X-User-Roles / X-Tenant-Id). OPERATOR
#              surface: platform_manager cross-tenant; admin/tenant_admin own
#              tenant. The snapshot trigger (metering pass) is platform_manager
#              only (invoked by the periodic CronJob). Reporting/governance —
#              exposes sizes only, never content.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ay_platform_core.c8_llm.storage.models import (
    ProjectStorageReport,
    ProjectStorageSeries,
    StorageSnapshotResult,
    TenantStorageReport,
)
from ay_platform_core.c8_llm.storage.service import StorageService

router = APIRouter(tags=["storage"])

_OPERATOR_ROLES: tuple[str, ...] = ("platform_manager", "admin", "tenant_admin")
_PLATFORM_ROLES: tuple[str, ...] = ("platform_manager",)


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


def _is_platform_manager(x_user_roles: str | None) -> bool:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    return "platform_manager" in roles


def _report_scope(
    x_user_roles: str | None, x_tenant_id: str | None, query_tenant_id: str | None
) -> str | None:
    """Tenant a report is confined to: platform_manager is cross-tenant (uses
    the optional `?tenant_id=` filter, else all); a tenant operator is FORCED to
    its own `X-Tenant-Id`."""
    if _is_platform_manager(x_user_roles):
        return query_tenant_id
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header missing for a tenant-scoped operator",
        )
    return x_tenant_id


def get_storage_service(request: Request) -> StorageService:
    service = getattr(request.app.state, "storage_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="storage metering not configured (no object store)",
        )
    return service  # type: ignore[no-any-return]


@router.get("/admin/v1/storage/projects", response_model=ProjectStorageReport)
async def get_project_storage(
    tenant_id: str | None = None,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    service: StorageService = Depends(get_storage_service),
) -> ProjectStorageReport:
    """Current disk occupation per project (live MinIO measure). platform_manager
    sees ALL tenants (optional `?tenant_id=`); admin/tenant_admin its own."""
    _require_role(x_user_roles, _OPERATOR_ROLES)
    scope = _report_scope(x_user_roles, x_tenant_id, tenant_id)
    return await service.project_storage_report(tenant_id=scope)


@router.get("/admin/v1/storage/tenants", response_model=TenantStorageReport)
async def get_tenant_storage(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: StorageService = Depends(get_storage_service),
) -> TenantStorageReport:
    """Current disk occupation per TENANT (sum of its projects), live-measured.
    platform_manager only (cross-tenant)."""
    _require_role(x_user_roles, _PLATFORM_ROLES)
    return await service.tenant_storage_report()


@router.get(
    "/admin/v1/storage/projects/{project_id}/series",
    response_model=ProjectStorageSeries,
)
async def get_project_storage_series(
    project_id: str,
    tenant_id: str,
    window: str = "month",
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    service: StorageService = Depends(get_storage_service),
) -> ProjectStorageSeries:
    """A project's storage time-series over `window` + its current occupation.
    `tenant_id` (the project's tenant) is required; a tenant operator may only
    query its own tenant (403 otherwise)."""
    _require_role(x_user_roles, _OPERATOR_ROLES)
    if not _is_platform_manager(x_user_roles) and tenant_id != x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="project is not in your tenant",
        )
    return await service.project_series(tenant_id, project_id, window)


@router.post("/admin/v1/storage/snapshot", response_model=StorageSnapshotResult)
async def trigger_storage_snapshot(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: StorageService = Depends(get_storage_service),
) -> StorageSnapshotResult:
    """Run a metering pass — measure every project + append one snapshot.
    platform_manager only (invoked by the periodic metering CronJob)."""
    _require_role(x_user_roles, _PLATFORM_ROLES)
    return await service.run_snapshot()
