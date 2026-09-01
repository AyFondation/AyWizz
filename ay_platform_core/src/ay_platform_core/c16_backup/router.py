# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/router.py
# Description: FastAPI surface for C16 (900-SPEC §4). Project-scoped under
#              `/api/v1/projects/{project_id}/backups` so C2 forward-auth
#              resolves the caller's role ON that project. Authorised roles
#              (R-900-011): project_owner (own project), tenant_admin (their
#              tenant's projects), platform_manager (all). Cross-tenant/project
#              isolation is enforced by matching the record's ids.
#
# @relation implements:R-900-002
# @relation implements:R-900-005
# @relation implements:R-900-006
# @relation implements:R-900-007
# @relation implements:R-900-008
# @relation implements:R-900-011
# =============================================================================

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from ay_platform_core.c16_backup.models import (
    BackupRecord,
    RestoreReport,
    RestoreRequest,
)
from ay_platform_core.c16_backup.service import BackupService, RestoreValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

router = APIRouter(tags=["backups"])

# Roles allowed on a backup operation. `project_owner` is resolved by C2 for the
# path's {project_id}; `tenant_admin`/`admin` + `platform_manager` are global.
_ROLES = ("project_owner", "tenant_admin", "admin", "platform_manager")


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-User-Id header missing")
    return x_user_id


def _require_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-Tenant-Id header missing")
    return x_tenant_id


def _roles(x_user_roles: str | None) -> set[str]:
    return {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}


def _require_role(x_user_roles: str | None) -> set[str]:
    roles = _roles(x_user_roles)
    if not roles.intersection(_ROLES):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"requires one of: {', '.join(_ROLES)}"
        )
    return roles


def _service(request: Request) -> BackupService:
    return request.app.state.backup_service  # type: ignore[no-any-return]


def _authorize_record(
    record: BackupRecord | None, *, project_id: str, tenant_id: str, roles: set[str],
) -> BackupRecord:
    """404 for an unknown record OR one outside the caller's authorised scope —
    isolation SHALL NOT leak the existence of another tenant/project's backup."""
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    if "platform_manager" in roles:
        return record  # super user: all backups
    if record.tenant_id != tenant_id or record.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    return record


@router.post(
    "/api/v1/projects/{project_id}/backups",
    response_model=BackupRecord, status_code=status.HTTP_201_CREATED,
)
async def create_snapshot(
    project_id: str,
    user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> BackupRecord:
    """Snapshot one project into a stored archive (R-900-002)."""
    _require_role(x_user_roles)
    return await asyncio.to_thread(
        service.create_project_snapshot,
        tenant_id=tenant_id, project_id=project_id, created_by=user,
    )


@router.get(
    "/api/v1/projects/{project_id}/backups",
    response_model=list[BackupRecord],
)
async def list_backups(
    project_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> list[BackupRecord]:
    """List a project's backups, newest first (R-900-005)."""
    _require_role(x_user_roles)
    return await asyncio.to_thread(
        service.list_project_backups, tenant_id=tenant_id, project_id=project_id,
    )


@router.get("/api/v1/projects/{project_id}/backups/{backup_id}/download")
async def download_backup(
    project_id: str,
    backup_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> StreamingResponse:
    """Stream a stored archive as `.tar.gz` (R-900-006)."""
    roles = _require_role(x_user_roles)
    result = await asyncio.to_thread(service.get_backup, backup_id)
    record = _authorize_record(
        None if result is None else result[0],
        project_id=project_id, tenant_id=tenant_id, roles=roles,
    )
    gz = result[1]  # type: ignore[index]

    def _iter() -> Iterator[bytes]:
        yield gz

    return StreamingResponse(
        _iter(), media_type="application/gzip",
        headers={"Content-Disposition":
                 f'attachment; filename="{record.backup_id}.tar.gz"'},
    )


@router.post(
    "/api/v1/projects/{project_id}/backups/archives",
    response_model=BackupRecord, status_code=status.HTTP_201_CREATED,
)
async def upload_archive(
    project_id: str,
    user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    file: UploadFile = File(...),
    service: BackupService = Depends(_service),
) -> BackupRecord:
    """Upload + register an archive for later restore (R-900-007)."""
    _require_role(x_user_roles)
    data = await file.read()
    try:
        return await asyncio.to_thread(
            service.register_uploaded_archive,
            data, tenant_id=tenant_id, created_by=user,
        )
    except Exception as exc:
        # A malformed / unsupported / corrupt archive → 422, never a 500.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid archive: {exc}"
        ) from exc


@router.post(
    "/api/v1/projects/{project_id}/backups/{backup_id}/restore",
    response_model=RestoreReport,
)
async def restore_backup(
    project_id: str,
    backup_id: str,
    body: RestoreRequest,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> RestoreReport:
    """Restore a backup as a NEW project of the caller's tenant (R-900-008)."""
    roles = _require_role(x_user_roles)
    result = await asyncio.to_thread(service.get_backup, backup_id)
    _authorize_record(
        None if result is None else result[0],
        project_id=project_id, tenant_id=tenant_id, roles=roles,
    )
    gz = result[1]  # type: ignore[index]
    try:
        return await asyncio.to_thread(
            service.restore_project_as_new,
            gz, target_tenant_id=tenant_id, dry_run=body.dry_run,
            new_project_id=body.new_project_id,
        )
    except RestoreValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Tenant-scope surface — the WHOLE-tenant archive (R-900-011: tenant_admin of
# THAT tenant, or platform_manager). project_owner is NOT sufficient here.
# ---------------------------------------------------------------------------

_TENANT_ROLES = ("tenant_admin", "admin", "platform_manager")


def _require_tenant_scope(
    path_tenant: str, caller_tenant: str, x_user_roles: str | None,
) -> set[str]:
    roles = _roles(x_user_roles)
    if not roles.intersection(_TENANT_ROLES):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"requires one of: {', '.join(_TENANT_ROLES)}"
        )
    # tenant_admin/admin are bound to their own tenant; platform_manager is not.
    # A cross-tenant attempt is a 404 (isolation — SHALL NOT leak existence),
    # NOT a 403: the role gate has cleared, only the scope is out of bounds.
    if "platform_manager" not in roles and path_tenant != caller_tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return roles


@router.post(
    "/api/v1/tenants/{tenant_id}/backups",
    response_model=BackupRecord, status_code=status.HTTP_201_CREATED,
)
async def create_tenant_snapshot(
    tenant_id: str,
    user: str = Depends(_require_actor),
    caller_tenant: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> BackupRecord:
    """Snapshot a WHOLE tenant into one archive (R-900-002)."""
    _require_tenant_scope(tenant_id, caller_tenant, x_user_roles)
    return await asyncio.to_thread(
        service.create_tenant_snapshot, tenant_id=tenant_id, created_by=user,
    )


@router.get(
    "/api/v1/tenants/{tenant_id}/backups", response_model=list[BackupRecord],
)
async def list_tenant_backups(
    tenant_id: str,
    _user: str = Depends(_require_actor),
    caller_tenant: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> list[BackupRecord]:
    """List ALL of a tenant's backups (project + tenant scope) (R-900-005)."""
    _require_tenant_scope(tenant_id, caller_tenant, x_user_roles)
    return await asyncio.to_thread(service.list_tenant_backups, tenant_id=tenant_id)


@router.get("/api/v1/tenants/{tenant_id}/backups/{backup_id}/download")
async def download_tenant_backup(
    tenant_id: str,
    backup_id: str,
    _user: str = Depends(_require_actor),
    caller_tenant: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> StreamingResponse:
    """Stream a tenant-owned archive (R-900-006)."""
    _require_tenant_scope(tenant_id, caller_tenant, x_user_roles)
    result = await asyncio.to_thread(service.get_backup, backup_id)
    if result is None or result[0].tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    record, gz = result

    def _iter() -> Iterator[bytes]:
        yield gz

    return StreamingResponse(
        _iter(), media_type="application/gzip",
        headers={"Content-Disposition":
                 f'attachment; filename="{record.backup_id}.tar.gz"'},
    )


@router.post(
    "/api/v1/tenants/{tenant_id}/backups/archives",
    response_model=BackupRecord, status_code=status.HTTP_201_CREATED,
)
async def upload_tenant_archive(
    tenant_id: str,
    user: str = Depends(_require_actor),
    caller_tenant: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    file: UploadFile = File(...),
    service: BackupService = Depends(_service),
) -> BackupRecord:
    """Upload + register an archive under a tenant (R-900-007)."""
    _require_tenant_scope(tenant_id, caller_tenant, x_user_roles)
    data = await file.read()
    try:
        return await asyncio.to_thread(
            service.register_uploaded_archive,
            data, tenant_id=tenant_id, created_by=user,
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid archive: {exc}"
        ) from exc


@router.post(
    "/api/v1/tenants/{tenant_id}/backups/{backup_id}/restore",
    response_model=RestoreReport,
)
async def restore_tenant_backup(
    tenant_id: str,
    backup_id: str,
    body: RestoreRequest,
    _user: str = Depends(_require_actor),
    caller_tenant: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: BackupService = Depends(_service),
) -> RestoreReport:
    """Restore a tenant archive as a NEW tenant (R-900-008)."""
    _require_tenant_scope(tenant_id, caller_tenant, x_user_roles)
    result = await asyncio.to_thread(service.get_backup, backup_id)
    if result is None or result[0].tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found")
    gz = result[1]
    try:
        return await asyncio.to_thread(
            service.restore_tenant_as_new, gz, dry_run=body.dry_run,
        )
    except RestoreValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
        ) from exc
