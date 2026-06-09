# =============================================================================
# File: admin_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/admin_router.py
# Description: Tenant lifecycle endpoints (Phase A of v1 functional plan).
#              Mounted under `/admin/*` by the C2 app factory; gated by
#              `tenant_manager` per E-100-002 v2 (super-root, content-blind).
#
#              Tenant-content endpoints (users, sessions, projects) live
#              elsewhere and are NOT in this router — `tenant_manager`
#              MUST NOT have access to tenant content.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ay_platform_core.c2_auth.models import (
    JWTClaims,
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


def _require_tenant_manager(
    claims: JWTClaims = Depends(_get_current_claims),
) -> JWTClaims:
    if RBACGlobalRole.TENANT_MANAGER not in claims.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_manager role required",
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
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> TenantPublic:
    """Create a new tenant. tenant_manager only."""
    return await service.create_tenant(body)


@router.get("/tenants", response_model=TenantList)
async def list_tenants(
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> TenantList:
    """List every tenant on the platform. tenant_manager only."""
    return TenantList(items=await service.list_tenants())


@router.delete(
    "/tenants/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_tenant(
    tenant_id: str,
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> None:
    """Delete a tenant. tenant_manager only. NOT cascade — tenant content
    deletion (users, projects, sources) is handled by separate flows."""
    await service.delete_tenant(tenant_id)


@router.post("/tenants/{tenant_id}/deactivate", response_model=TenantPublic)
async def deactivate_tenant(
    tenant_id: str,
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> TenantPublic:
    """Deactivate a tenant — its members are refused login. tenant_manager only."""
    return await service.set_tenant_active(tenant_id, active=False)


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantPublic)
async def reactivate_tenant(
    tenant_id: str,
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> TenantPublic:
    """Reactivate a previously deactivated tenant. tenant_manager only."""
    return await service.set_tenant_active(tenant_id, active=True)


# ---------------------------------------------------------------------------
# Cross-tenant user oversight (tenant_manager — E-100-002 v3 platform operator)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserList)
async def list_users(
    tenant_id: str | None = None,
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> UserList:
    """List users across ALL tenants (optionally filtered by `tenant_id`).
    Read-only oversight — user create/delete stays with the tenant's own admin."""
    return UserList(items=await service.list_users(tenant_id))


@router.post("/users/{user_id}/deactivate", response_model=UserPublic)
async def deactivate_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Deactivate a user (cross-tenant). tenant_manager only."""
    return await service.set_user_active(user_id, active=False)


@router.post("/users/{user_id}/reactivate", response_model=UserPublic)
async def reactivate_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_tenant_manager),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Reactivate a user (cross-tenant). tenant_manager only."""
    return await service.set_user_active(user_id, active=True)
