# =============================================================================
# File: catalog_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/catalog_router.py
# Description: FastAPI APIRouter for the per-tenant LLM catalogue admin surface.
#              Tenant-scoped (X-Tenant-Id forward-auth header) and gated to a
#              tenant's content owners — `admin` / `tenant_admin`. Per
#              E-100-002 v2 the catalogue is TENANT CONTENT, so `tenant_manager`
#              (content-blind super-root) is EXCLUDED: a tenant_manager-only
#              caller fails the gate (it holds none of the accepted roles).
#
#              Carries no secret — catalogue rows never hold a key. Mounted by
#              the c8_admin app factory with prefix "".
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from ay_platform_core.c8_llm.registry.catalog_models import (
    ProjectModelsResponse,
    ProjectModelsUpdate,
    ResolvedModel,
    TenantCatalogListResponse,
    TenantCatalogModelPublic,
    TenantCatalogUpsert,
)
from ay_platform_core.c8_llm.registry.catalog_service import (
    ModelNotInRegistryError,
    TenantCatalogService,
)
from ay_platform_core.c8_llm.registry.models import ModelQuality

router = APIRouter(tags=["llm-catalog"])

_CATALOG_ROLES: tuple[str, ...] = ("admin", "tenant_admin")


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


def _require_role(x_user_roles: str | None, required: tuple[str, ...]) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


def get_catalog_service(request: Request) -> TenantCatalogService:
    return request.app.state.catalog_service  # type: ignore[no-any-return]


@router.get("/api/v1/llm/catalog", response_model=TenantCatalogListResponse)
async def list_catalog(
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: TenantCatalogService = Depends(get_catalog_service),
) -> TenantCatalogListResponse:
    """List the calling tenant's catalogue (joined with registry public info)."""
    _require_role(x_user_roles, _CATALOG_ROLES)
    return TenantCatalogListResponse(models=await service.list_catalog(tenant_id))


@router.get("/api/v1/llm/catalog/resolve", response_model=ResolvedModel)
async def resolve_model(
    model_quality: ModelQuality,
    require_vision: bool = False,
    require_tool_calling: bool = False,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_project_id: str | None = Header(default=None),
    service: TenantCatalogService = Depends(get_catalog_service),
) -> ResolvedModel:
    """Resolve the calling tenant's `model_quality` to a concrete model, SCOPED
    to the project's model set when `X-Project-Id` is supplied (explicit list,
    else tenant defaults, else the whole catalogue). 404 when no qualifying
    model (no silent downgrade)."""
    resolved = await service.resolve(
        tenant_id,
        model_quality,
        project_id=x_project_id,
        require_vision=require_vision,
        require_tool_calling=require_tool_calling,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no enabled model of quality '{model_quality.value}' "
                f"(vision={require_vision}, tool_calling={require_tool_calling}) "
                f"in the tenant catalogue"
            ),
        )
    return resolved


@router.put(
    "/api/v1/llm/catalog/{model_id}", response_model=TenantCatalogModelPublic
)
async def upsert_catalog_model(
    model_id: str,
    body: TenantCatalogUpsert,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: TenantCatalogService = Depends(get_catalog_service),
) -> TenantCatalogModelPublic:
    """Add/configure a registry model in the calling tenant's catalogue, by its
    stable model_id. 404 when the model_id is unknown to the platform registry."""
    _require_role(x_user_roles, _CATALOG_ROLES)
    try:
        return await service.upsert_model(tenant_id, model_id, body)
    except ModelNotInRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"model id not in platform registry: {model_id}",
        ) from exc


@router.delete(
    "/api/v1/llm/catalog/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def remove_catalog_model(
    model_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: TenantCatalogService = Depends(get_catalog_service),
) -> None:
    """Remove a model from the calling tenant's catalogue. 404 if absent."""
    _require_role(x_user_roles, _CATALOG_ROLES)
    removed = await service.remove_model(tenant_id, model_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"model not in tenant catalogue: {model_id}",
        )


@router.get(
    "/api/v1/llm/projects/{project_id}/models", response_model=ProjectModelsResponse
)
async def get_project_models(
    project_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: TenantCatalogService = Depends(get_catalog_service),
) -> ProjectModelsResponse:
    """The project's EFFECTIVE model list (explicit, else tenant defaults).
    admin / tenant_admin only."""
    _require_role(x_user_roles, _CATALOG_ROLES)
    return await service.get_project_models(tenant_id, project_id)


@router.put(
    "/api/v1/llm/projects/{project_id}/models", response_model=ProjectModelsResponse
)
async def set_project_models(
    project_id: str,
    body: ProjectModelsUpdate,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: TenantCatalogService = Depends(get_catalog_service),
) -> ProjectModelsResponse:
    """Set the project's EXPLICIT model list (subset of the tenant catalogue).
    404 when an id is not in the catalogue. admin / tenant_admin only."""
    _require_role(x_user_roles, _CATALOG_ROLES)
    try:
        return await service.set_project_models(tenant_id, project_id, body.model_ids)
    except ModelNotInRegistryError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"model not in tenant catalogue: {exc}",
        ) from exc
