# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/router.py
# Description: FastAPI APIRouter for the platform LLM MODEL registry admin
#              surface. Identity arrives via Traefik forward-auth headers
#              (X-User-Id, X-User-Roles); gated to `tenant_manager`. v2: models
#              are addressed by their STABLE `model_id` — POST creates (mints
#              the id), PUT/{model_id} updates (alias + every attribute may
#              change), DELETE/{model_id} removes. The API key NO LONGER lives
#              here — it belongs to the provider (see provider_router.py).
#              Mounted by the c8_admin app factory with prefix "".
# =============================================================================

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)

from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    LLMRegistryListResponse,
    LLMRegistryPublic,
)
from ay_platform_core.c8_llm.registry.service import (
    LLMRegistryService,
    ModelNotFoundError,
)

router = APIRouter(tags=["llm-registry"])

_REGISTRY_ROLES: tuple[str, ...] = ("tenant_manager",)


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


def get_registry_service(request: Request) -> LLMRegistryService:
    """Resolve the registry service from app state (set by the app factory)."""
    return request.app.state.registry_service  # type: ignore[no-any-return]


@router.get("/admin/v1/llm/registry", response_model=LLMRegistryListResponse)
async def list_registry(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMRegistryService = Depends(get_registry_service),
) -> LLMRegistryListResponse:
    """List every registered model (public projection; no secret)."""
    _require_role(x_user_roles, _REGISTRY_ROLES)
    return LLMRegistryListResponse(models=await service.list_models())


@router.post(
    "/admin/v1/llm/registry",
    response_model=LLMRegistryPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_registry_model(
    body: LLMModelUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMRegistryService = Depends(get_registry_service),
) -> LLMRegistryPublic:
    """Create a model (mints its stable model_id). 409 on a duplicate alias."""
    _require_role(x_user_roles, _REGISTRY_ROLES)
    return await service.create_model(body)


@router.put("/admin/v1/llm/registry/{model_id}", response_model=LLMRegistryPublic)
async def update_registry_model(
    model_id: str,
    body: LLMModelUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMRegistryService = Depends(get_registry_service),
) -> LLMRegistryPublic:
    """Update a model's attributes (alias + provider + cost + capabilities +
    quality + enabled). The model_id — and all tenant/project references — is
    unchanged. 404 unknown id, 409 alias clash."""
    _require_role(x_user_roles, _REGISTRY_ROLES)
    try:
        return await service.update_model(model_id, body)
    except ModelNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown model id: {model_id}",
        ) from exc


@router.delete(
    "/admin/v1/llm/registry/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_registry_model(
    model_id: str,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMRegistryService = Depends(get_registry_service),
) -> None:
    """Delete a model by id. tenant_manager only. 404 if unknown."""
    _require_role(x_user_roles, _REGISTRY_ROLES)
    if not await service.delete_model(model_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown model id: {model_id}",
        )
