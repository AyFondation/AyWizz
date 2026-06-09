# =============================================================================
# File: provider_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/provider_router.py
# Description: FastAPI APIRouter for the LLM PROVIDER registry (endpoint +
#              credential layer), tenant_manager only, forward-auth gated. A
#              provider is addressed by its stable `provider_id`. The API key is
#              WRITE-ONLY on its own dedicated endpoint (encrypted, never echoed;
#              503 without a master key). Mounted by the c8_admin app factory.
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

from ay_platform_core.c8_llm.registry.provider_models import (
    LLMProviderApiKeyUpdate,
    LLMProviderListResponse,
    LLMProviderPublic,
    LLMProviderUpsert,
)
from ay_platform_core.c8_llm.registry.provider_service import (
    KeyManagementUnavailableError,
    LLMProviderService,
    ProviderNotFoundError,
)

router = APIRouter(tags=["llm-provider"])

_PROVIDER_ROLES: tuple[str, ...] = ("tenant_manager",)


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


def get_provider_service(request: Request) -> LLMProviderService:
    return request.app.state.provider_service  # type: ignore[no-any-return]


@router.get("/admin/v1/llm/providers", response_model=LLMProviderListResponse)
async def list_providers(
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMProviderService = Depends(get_provider_service),
) -> LLMProviderListResponse:
    """List every provider (public projection; key write-only)."""
    _require_role(x_user_roles, _PROVIDER_ROLES)
    return LLMProviderListResponse(providers=await service.list_providers())


@router.post(
    "/admin/v1/llm/providers",
    response_model=LLMProviderPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider(
    body: LLMProviderUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMProviderService = Depends(get_provider_service),
) -> LLMProviderPublic:
    """Create a provider (mints its id). 409 on a duplicate name."""
    _require_role(x_user_roles, _PROVIDER_ROLES)
    return await service.create_provider(body)


@router.put("/admin/v1/llm/providers/{provider_id}", response_model=LLMProviderPublic)
async def update_provider(
    provider_id: str,
    body: LLMProviderUpsert,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMProviderService = Depends(get_provider_service),
) -> LLMProviderPublic:
    """Update a provider's name / base_url / wire_format (key preserved)."""
    _require_role(x_user_roles, _PROVIDER_ROLES)
    try:
        return await service.update_provider(provider_id, body)
    except ProviderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown provider id: {provider_id}",
        ) from exc


@router.put(
    "/admin/v1/llm/providers/{provider_id}/api-key",
    response_model=LLMProviderPublic,
)
async def set_provider_api_key(
    provider_id: str,
    body: LLMProviderApiKeyUpdate,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMProviderService = Depends(get_provider_service),
) -> LLMProviderPublic:
    """Set (write-only) the encrypted API key for a provider. 404 unknown id,
    503 without a master key."""
    _require_role(x_user_roles, _PROVIDER_ROLES)
    try:
        return await service.set_api_key(provider_id, body.api_key)
    except ProviderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown provider id: {provider_id}",
        ) from exc
    except KeyManagementUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="key management unavailable: no master key configured",
        ) from exc


@router.delete(
    "/admin/v1/llm/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_provider(
    provider_id: str,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: LLMProviderService = Depends(get_provider_service),
) -> None:
    """Delete a provider by id. tenant_manager only. 404 if unknown."""
    _require_role(x_user_roles, _PROVIDER_ROLES)
    if not await service.delete_provider(provider_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown provider id: {provider_id}",
        )
