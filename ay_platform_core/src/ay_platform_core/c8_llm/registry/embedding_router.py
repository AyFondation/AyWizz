# =============================================================================
# File: embedding_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_router.py
# Description: FastAPI APIRouter for the platform EMBEDDING registry
#              (providers + models), platform_manager only, forward-auth gated.
#              Mirrors the chat provider/model routers: stable ids, write-only
#              encrypted provider key (503 without a master key), provider
#              delete guarded while models reference it. Mounted by the c8_admin
#              app factory.
#
# @relation implements:R-400-226
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingModelListResponse,
    EmbeddingModelPublic,
    EmbeddingModelUpsert,
    EmbeddingProviderApiKeyUpdate,
    EmbeddingProviderListResponse,
    EmbeddingProviderPublic,
    EmbeddingProviderUpsert,
)
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelNotFoundError,
    EmbeddingModelService,
    EmbeddingProviderNotFoundError,
    EmbeddingProviderService,
    KeyManagementUnavailableError,
)

router = APIRouter(tags=["embedding-registry"])

_ROLES: tuple[str, ...] = ("platform_manager",)


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_role(x_user_roles: str | None) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(_ROLES):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(_ROLES)}",
        )


def get_provider_service(request: Request) -> EmbeddingProviderService:
    return request.app.state.embedding_provider_service  # type: ignore[no-any-return]


def get_model_service(request: Request) -> EmbeddingModelService:
    return request.app.state.embedding_model_service  # type: ignore[no-any-return]


# --- Providers --------------------------------------------------------------


@router.get(
    "/admin/v1/llm/embedding-providers", response_model=EmbeddingProviderListResponse
)
async def list_providers(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingProviderService = Depends(get_provider_service),
) -> EmbeddingProviderListResponse:
    _require_role(x_user_roles)
    return EmbeddingProviderListResponse(providers=await service.list_providers())


@router.post(
    "/admin/v1/llm/embedding-providers",
    response_model=EmbeddingProviderPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider(
    body: EmbeddingProviderUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingProviderService = Depends(get_provider_service),
) -> EmbeddingProviderPublic:
    _require_role(x_user_roles)
    return await service.create_provider(body)


@router.put(
    "/admin/v1/llm/embedding-providers/{provider_id}",
    response_model=EmbeddingProviderPublic,
)
async def update_provider(
    provider_id: str,
    body: EmbeddingProviderUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingProviderService = Depends(get_provider_service),
) -> EmbeddingProviderPublic:
    _require_role(x_user_roles)
    try:
        return await service.update_provider(provider_id, body)
    except EmbeddingProviderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown embedding provider id: {provider_id}",
        ) from exc


@router.put(
    "/admin/v1/llm/embedding-providers/{provider_id}/api-key",
    response_model=EmbeddingProviderPublic,
)
async def set_provider_api_key(
    provider_id: str,
    body: EmbeddingProviderApiKeyUpdate,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingProviderService = Depends(get_provider_service),
) -> EmbeddingProviderPublic:
    _require_role(x_user_roles)
    try:
        return await service.set_api_key(provider_id, body.api_key)
    except EmbeddingProviderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown embedding provider id: {provider_id}",
        ) from exc
    except KeyManagementUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="embedding key management disabled (no AY_SECRET_MASTER_KEY)",
        ) from exc


@router.delete(
    "/admin/v1/llm/embedding-providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_provider(
    provider_id: str,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingProviderService = Depends(get_provider_service),
) -> Response:
    _require_role(x_user_roles)
    await service.delete_provider(provider_id)  # 409 inside if models reference it
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Models -----------------------------------------------------------------


@router.get(
    "/admin/v1/llm/embedding-models", response_model=EmbeddingModelListResponse
)
async def list_models(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingModelService = Depends(get_model_service),
) -> EmbeddingModelListResponse:
    _require_role(x_user_roles)
    return EmbeddingModelListResponse(models=await service.list_models())


@router.post(
    "/admin/v1/llm/embedding-models",
    response_model=EmbeddingModelPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    body: EmbeddingModelUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingModelService = Depends(get_model_service),
) -> EmbeddingModelPublic:
    _require_role(x_user_roles)
    return await service.create_model(body)


@router.put(
    "/admin/v1/llm/embedding-models/{model_id}", response_model=EmbeddingModelPublic
)
async def update_model(
    model_id: str,
    body: EmbeddingModelUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingModelService = Depends(get_model_service),
) -> EmbeddingModelPublic:
    _require_role(x_user_roles)
    try:
        return await service.update_model(model_id, body)
    except EmbeddingModelNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown embedding model id: {model_id}",
        ) from exc


@router.delete(
    "/admin/v1/llm/embedding-models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_model(
    model_id: str,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: EmbeddingModelService = Depends(get_model_service),
) -> Response:
    _require_role(x_user_roles)
    await service.delete_model(model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
