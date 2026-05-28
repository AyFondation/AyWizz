# =============================================================================
# File: projects_router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c2_auth/projects_router.py
# Description: Project lifecycle endpoints (Phase A of v1 functional plan).
#              Mounted under `/api/v1/projects` by the C2 app factory.
#
#              Identity model: forward-auth headers (`X-User-Id`,
#              `X-Tenant-Id`, `X-User-Roles`) — same convention as
#              C3/C5/C6/C7 — NOT Bearer JWT. Traefik propagates these
#              from the JWT in production. Project lifecycle is content
#              of a tenant, so `tenant_manager` is EXCLUDED.
#
#              v2: adds `GET /{pid}` (any tenant member) and
#              `PATCH /{pid}` (admin / tenant_admin / project_owner)
#              so the UX can read + tune the per-project system prompt.
#
#              Role gates per endpoint:
#                POST   /                     → admin / tenant_admin
#                GET    /                     → any authenticated user (filtered to caller's tenant)
#                GET    /{project_id}         → any tenant member (filtered to caller's tenant)
#                PATCH  /{project_id}         → admin / tenant_admin / project_owner
#                DELETE /{project_id}         → admin / tenant_admin
#                POST   /{pid}/members/{uid}  → admin / tenant_admin / project_owner
#                DELETE /{pid}/members/{uid}  → admin / tenant_admin / project_owner
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from ay_platform_core.c2_auth.models import (
    ProjectCreate,
    ProjectList,
    ProjectMemberGrant,
    ProjectPublic,
    ProjectUpdate,
)
from ay_platform_core.c2_auth.service import AuthService, get_service

router = APIRouter(tags=["projects"])


# ---------------------------------------------------------------------------
# Forward-auth header helpers
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


def _parse_roles(x_user_roles: str | None) -> set[str]:
    return {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}


def _require_role_intersect(
    x_user_roles: str | None, accepted: tuple[str, ...]
) -> None:
    roles = _parse_roles(x_user_roles)
    if "tenant_manager" in roles and not roles.intersection(accepted):
        # Explicit content-blindness assertion: tenant_manager-only callers
        # SHALL be rejected even if they happen to be in `accepted` (they
        # never are, but the intent is enforced here).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_manager has no access to tenant content (E-100-002 v2)",
        )
    if not roles.intersection(accepted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(accepted)}",
        )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ProjectPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    body: ProjectCreate,
    actor_id: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> ProjectPublic:
    _require_role_intersect(x_user_roles, ("admin", "tenant_admin"))
    return await service.create_project(body, tenant_id, actor_id)


@router.get("", response_model=ProjectList)
async def list_projects(
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> ProjectList:
    """List projects in the caller's tenant. Any authenticated user can
    call this; results are scoped to the X-Tenant-Id header. tenant_manager
    is rejected — listing tenant projects is tenant content."""
    if "tenant_manager" in _parse_roles(x_user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_manager has no access to tenant content (E-100-002 v2)",
        )
    return ProjectList(items=await service.list_projects(tenant_id))


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> ProjectPublic:
    """Read a single project (any tenant member). The response carries
    the EFFECTIVE `system_prompt` (override OR C2 default) plus
    `system_prompt_is_default`, so the UX has everything it needs in
    one round-trip. tenant_manager is rejected as project content
    is out of scope per E-100-002 v2."""
    if "tenant_manager" in _parse_roles(x_user_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_manager has no access to tenant content (E-100-002 v2)",
        )
    return await service.get_project(project_id, tenant_id)


@router.patch("/{project_id}", response_model=ProjectPublic)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> ProjectPublic:
    """Update mutable project fields (name + system_prompt).
    Restricted to admin / tenant_admin (tenant-level admins) and
    project_owner (the project's own steward). Body semantics for
    `system_prompt` : missing/null = no change ; empty string = clear
    override (revert to C2 default) ; non-empty string = set override."""
    _require_role_intersect(
        x_user_roles, ("admin", "tenant_admin", "project_owner"),
    )
    return await service.update_project(project_id, tenant_id, body)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_project(
    project_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> None:
    _require_role_intersect(x_user_roles, ("admin", "tenant_admin"))
    await service.delete_project(project_id, tenant_id)


# ---------------------------------------------------------------------------
# Project membership
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def grant_project_member(
    project_id: str,
    user_id: str,
    body: ProjectMemberGrant,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> None:
    _require_role_intersect(
        x_user_roles, ("admin", "tenant_admin", "project_owner"),
    )
    await service.grant_project_member(project_id, tenant_id, user_id, body.role)


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def revoke_project_member(
    project_id: str,
    user_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> None:
    _require_role_intersect(
        x_user_roles, ("admin", "tenant_admin", "project_owner"),
    )
    await service.revoke_project_member(project_id, tenant_id, user_id)
