# =============================================================================
# File: admin_router.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c2_auth/admin_router.py
# Description: Platform-operator endpoints. Mounted under `/admin/*` by the C2
#              app factory; gated by `platform_manager` (E-100-002 v3/v4,
#              super-root, content-blind).
#
#              v2 (E-100-002 v4): adds project GOVERNANCE — cross-tenant
#              list, lifecycle status (activate/deactivate/archive), ACL read,
#              and cross-tenant grant/revoke. These touch the project
#              GOVERNANCE object (metadata/status/ACL) only; project CONTENT
#              (conversations, sources, requirements, runs, artifacts) stays
#              in content-blind routers the platform_manager cannot reach.
#
#              v3 (E-100-002 v7): the tenant operator `admin` (= tenant_admin)
#              becomes the tenant-scoped mirror of platform_manager. Project
#              governance + USER oversight (list / deactivate / reactivate)
#              accept `admin`, confined to its OWN tenant (`_require_operator`,
#              `_require_project_operator`, `_require_user_operator`). Tenant
#              CRUD stays platform_manager-only. `admin` remains content-blind.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ay_platform_core.c2_auth.models import (
    JWTClaims,
    ProjectList,
    ProjectMemberGrant,
    ProjectMemberList,
    ProjectPublic,
    ProjectStatus,
    RBACGlobalRole,
    TenantCreate,
    TenantList,
    TenantPublic,
    UserList,
    UserPublic,
)
from ay_platform_core.c2_auth.service import AuthService, get_service

router = APIRouter(tags=["admin"])
_bearer = HTTPBearer()


async def _get_current_claims(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    service: AuthService = Depends(get_service),
) -> JWTClaims:
    return await service.verify_token(credentials.credentials)


def _require_platform_manager(
    claims: JWTClaims = Depends(_get_current_claims),
) -> JWTClaims:
    if RBACGlobalRole.PLATFORM_MANAGER not in claims.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform_manager role required",
        )
    return claims


_TENANT_OPERATOR_ROLES = frozenset(
    {RBACGlobalRole.ADMIN, RBACGlobalRole.TENANT_ADMIN}
)


def _require_operator(claims: JWTClaims = Depends(_get_current_claims)) -> JWTClaims:
    """Accept the platform operator (`platform_manager`, cross-tenant) OR a
    tenant operator (`admin` / `tenant_admin`, own tenant only). Endpoints scope
    their result to the caller's tenant when the caller is a tenant operator —
    see `_operator_tenant_scope` and `_require_project_operator` (E-100-002
    v7). Tenant operators are content-blind; this surface is governance only."""
    allowed = {RBACGlobalRole.PLATFORM_MANAGER, *_TENANT_OPERATOR_ROLES}
    if not (allowed & set(claims.roles)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform_manager or admin (tenant_admin) role required",
        )
    return claims


def _operator_tenant_scope(claims: JWTClaims) -> str | None:
    """The tenant an operator is confined to: `None` for `platform_manager`
    (cross-tenant), else the tenant operator's own `tenant_id`."""
    if RBACGlobalRole.PLATFORM_MANAGER in claims.roles:
        return None
    return claims.tenant_id


async def _require_project_operator(
    project_id: str,
    claims: JWTClaims = Depends(_require_operator),
    service: AuthService = Depends(get_service),
) -> JWTClaims:
    """Gate a per-project governance action: `platform_manager` passes for any
    project; a tenant operator (`admin`) passes ONLY for a project in its own
    tenant (404 if the project is absent, 403 if it belongs to another tenant)."""
    scope = _operator_tenant_scope(claims)
    if scope is not None:
        owner = await service.resolve_project_tenant(project_id)
        if owner is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        if owner != scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="project is not in your tenant",
            )
    return claims


async def _require_user_operator(
    user_id: str,
    claims: JWTClaims = Depends(_require_operator),
    service: AuthService = Depends(get_service),
) -> JWTClaims:
    """Gate a per-user governance action: `platform_manager` passes for any
    user; a tenant operator (`admin`) passes ONLY for a user in its own tenant
    (404 if the user is absent, 403 if they belong to another tenant)."""
    scope = _operator_tenant_scope(claims)
    if scope is not None:
        target = await service.get_user(user_id)  # 404 if absent
        if target.tenant_id != scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="user is not in your tenant",
            )
    return claims


# ---------------------------------------------------------------------------
# Tenant CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/tenants",
    response_model=TenantPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    body: TenantCreate,
    _claims: JWTClaims = Depends(_require_platform_manager),
    service: AuthService = Depends(get_service),
) -> TenantPublic:
    """Create a new tenant. platform_manager only."""
    return await service.create_tenant(body)


@router.get("/tenants", response_model=TenantList)
async def list_tenants(
    _claims: JWTClaims = Depends(_require_platform_manager),
    service: AuthService = Depends(get_service),
) -> TenantList:
    """List every tenant on the platform. platform_manager only."""
    return TenantList(items=await service.list_tenants())


@router.delete(
    "/tenants/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_tenant(
    tenant_id: str,
    _claims: JWTClaims = Depends(_require_platform_manager),
    service: AuthService = Depends(get_service),
) -> None:
    """Delete a tenant. platform_manager only. NOT cascade — tenant content
    deletion (users, projects, sources) is handled by separate flows."""
    await service.delete_tenant(tenant_id)


@router.post("/tenants/{tenant_id}/deactivate", response_model=TenantPublic)
async def deactivate_tenant(
    tenant_id: str,
    _claims: JWTClaims = Depends(_require_platform_manager),
    service: AuthService = Depends(get_service),
) -> TenantPublic:
    """Deactivate a tenant — its members are refused login. platform_manager only."""
    return await service.set_tenant_active(tenant_id, active=False)


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantPublic)
async def reactivate_tenant(
    tenant_id: str,
    _claims: JWTClaims = Depends(_require_platform_manager),
    service: AuthService = Depends(get_service),
) -> TenantPublic:
    """Reactivate a previously deactivated tenant. platform_manager only."""
    return await service.set_tenant_active(tenant_id, active=True)


# ---------------------------------------------------------------------------
# User oversight (operator — E-100-002 v7: platform_manager cross-tenant,
# admin/tenant_admin scoped to its own tenant)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserList)
async def list_users(
    tenant_id: str | None = None,
    claims: JWTClaims = Depends(_require_operator),
    service: AuthService = Depends(get_service),
) -> UserList:
    """List users. `platform_manager` sees ALL tenants (optionally filtered by
    `?tenant_id=`); `admin` sees ONLY its own tenant (the filter is forced).
    Read-only oversight — user create/delete stays with the tenant's own admin."""
    scope = _operator_tenant_scope(claims)
    effective_tenant = scope if scope is not None else tenant_id
    return UserList(items=await service.list_users(effective_tenant))


@router.post("/users/{user_id}/deactivate", response_model=UserPublic)
async def deactivate_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_user_operator),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Deactivate a user. platform_manager = any user; admin = own tenant only."""
    return await service.set_user_active(user_id, active=False)


@router.post("/users/{user_id}/reactivate", response_model=UserPublic)
async def reactivate_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_user_operator),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Reactivate a user. platform_manager = any user; admin = own tenant only."""
    return await service.set_user_active(user_id, active=True)


# ---------------------------------------------------------------------------
# Project GOVERNANCE (platform_manager — E-100-002 v4, cross-tenant)
# Metadata + lifecycle + ACL only. NEVER project content (content-blind).
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=ProjectList)
async def list_all_projects(
    tenant_id: str | None = None,
    claims: JWTClaims = Depends(_require_operator),
    service: AuthService = Depends(get_service),
) -> ProjectList:
    """List projects (metadata only) — governance object, exposes NO content.
    `platform_manager` sees ALL tenants (optionally `?tenant_id=`);
    `admin` sees ONLY its own tenant (the filter is forced)."""
    scope = _operator_tenant_scope(claims)
    effective_tenant = scope if scope is not None else tenant_id
    return ProjectList(items=await service.list_all_projects(effective_tenant))


@router.post("/projects/{project_id}/activate", response_model=ProjectPublic)
async def activate_project(
    project_id: str,
    claims: JWTClaims = Depends(_require_project_operator),
    service: AuthService = Depends(get_service),
) -> ProjectPublic:
    """Set a project back to `active` (from inactive or archived)."""
    return await service.set_project_status(
        project_id, ProjectStatus.ACTIVE, claims.sub
    )


@router.post("/projects/{project_id}/deactivate", response_model=ProjectPublic)
async def deactivate_project(
    project_id: str,
    claims: JWTClaims = Depends(_require_project_operator),
    service: AuthService = Depends(get_service),
) -> ProjectPublic:
    """Deactivate a project — its members are refused access to its content."""
    return await service.set_project_status(
        project_id, ProjectStatus.INACTIVE, claims.sub
    )


@router.post("/projects/{project_id}/archive", response_model=ProjectPublic)
async def archive_project(
    project_id: str,
    claims: JWTClaims = Depends(_require_project_operator),
    service: AuthService = Depends(get_service),
) -> ProjectPublic:
    """Archive a project — read-only freeze (content retained, no writes)."""
    return await service.set_project_status(
        project_id, ProjectStatus.ARCHIVED, claims.sub
    )


@router.get(
    "/projects/{project_id}/members", response_model=ProjectMemberList
)
async def list_project_access(
    project_id: str,
    _claims: JWTClaims = Depends(_require_project_operator),
    service: AuthService = Depends(get_service),
) -> ProjectMemberList:
    """Read a project's access-control list (members + project roles).
    Metadata only — exposes NO content."""
    return await service.list_project_members(project_id)


@router.post(
    "/projects/{project_id}/members/{user_id}",
    response_model=ProjectMemberList,
)
async def grant_project_access(
    project_id: str,
    user_id: str,
    body: ProjectMemberGrant,
    claims: JWTClaims = Depends(_require_project_operator),
    service: AuthService = Depends(get_service),
) -> ProjectMemberList:
    """Grant a user a project role (governance). platform_manager = any
    project; admin = its own tenant's projects only. Audited."""
    return await service.grant_project_access(
        project_id, user_id, body.role, claims.sub
    )


@router.delete(
    "/projects/{project_id}/members/{user_id}",
    response_model=ProjectMemberList,
)
async def revoke_project_access(
    project_id: str,
    user_id: str,
    claims: JWTClaims = Depends(_require_project_operator),
    service: AuthService = Depends(get_service),
) -> ProjectMemberList:
    """Revoke a user's project role (governance). platform_manager = any
    project; admin = its own tenant's projects only. Audited."""
    return await service.revoke_project_access(project_id, user_id, claims.sub)
