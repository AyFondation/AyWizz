# =============================================================================
# File: embedding_catalog_router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_catalog_router.py
# Description: FastAPI APIRouter for the per-tenant EMBEDDING catalogue (D-011).
#              Tenant-scoped (X-Tenant-Id forward-auth header), gated to the
#              content operators (admin / tenant_admin). Exposes which registry
#              embeddings a tenant makes available (+ default) and each project's
#              single selected embedding. DELETE-GUARD: 409 while a project uses
#              a model. Mounted by the c8_admin app factory.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from ay_platform_core.c8_llm.registry.embedding_catalog_models import (
    EmbeddingCatalogListResponse,
    EmbeddingCatalogModelPublic,
    EmbeddingCatalogUpsert,
    ProjectEmbeddingResponse,
    ProjectEmbeddingUpdate,
)
from ay_platform_core.c8_llm.registry.embedding_catalog_service import (
    EmbeddingCatalogService,
)
from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingModelListResponse,
)

router = APIRouter(tags=["embedding-catalog"])

_ROLES: tuple[str, ...] = ("admin", "tenant_admin")


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
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Tenant-Id header missing"
        )
    return x_tenant_id


def _require_role(x_user_roles: str | None) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(_ROLES)}",
        )


def get_service(request: Request) -> EmbeddingCatalogService:
    return request.app.state.embedding_catalog_service  # type: ignore[no-any-return]


@router.get("/api/v1/llm/embedding-catalog", response_model=EmbeddingCatalogListResponse)
async def list_catalog(
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingCatalogService = Depends(get_service),
) -> EmbeddingCatalogListResponse:
    _require_role(x_user_roles)
    return EmbeddingCatalogListResponse(models=await service.list_catalog(tenant_id))


@router.get(
    "/api/v1/llm/embedding-catalog/available",
    response_model=EmbeddingModelListResponse,
)
async def list_available(
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingCatalogService = Depends(get_service),
) -> EmbeddingModelListResponse:
    """Platform-registry embedding models the tenant hasn't catalogued yet — the
    add picker (the registry list is platform_manager-only)."""
    _require_role(x_user_roles)
    return EmbeddingModelListResponse(models=await service.list_available(tenant_id))


@router.put(
    "/api/v1/llm/embedding-catalog/{model_id}",
    response_model=EmbeddingCatalogModelPublic,
)
async def upsert_catalog(
    model_id: str,
    body: EmbeddingCatalogUpsert,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingCatalogService = Depends(get_service),
) -> EmbeddingCatalogModelPublic:
    _require_role(x_user_roles)
    return await service.upsert_catalog(tenant_id, model_id, body)


@router.delete(
    "/api/v1/llm/embedding-catalog/{model_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_catalog(
    model_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingCatalogService = Depends(get_service),
) -> Response:
    _require_role(x_user_roles)
    await service.remove_from_catalog(tenant_id, model_id)  # 409 inside if in use
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v1/llm/projects/{project_id}/embedding",
    response_model=ProjectEmbeddingResponse,
)
async def get_project_embedding(
    project_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingCatalogService = Depends(get_service),
) -> ProjectEmbeddingResponse:
    _require_role(x_user_roles)
    return await service.get_project_embedding(tenant_id, project_id)


@router.put(
    "/api/v1/llm/projects/{project_id}/embedding",
    response_model=ProjectEmbeddingResponse,
)
async def set_project_embedding(
    project_id: str,
    body: ProjectEmbeddingUpdate,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingCatalogService = Depends(get_service),
) -> ProjectEmbeddingResponse:
    _require_role(x_user_roles)
    return await service.set_project_embedding(tenant_id, project_id, body.model_id)
