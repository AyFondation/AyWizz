# =============================================================================
# File: preferences_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/preferences_router.py
# Description: Self-service preferences endpoints. Mounted under
#              `/api/v1/users/me/preferences` by the C2 app factory.
#              Open to any authenticated tenant member — preferences
#              are per-user data, the caller is the only legitimate
#              actor on their own record. tenant_manager is rejected
#              because the super-root has no content/identity inside
#              a tenant (E-100-002 v2).
#
#              Identity is read from the forward-auth header
#              `X-User-Id` propagated by Traefik. The body is the
#              `UserPreferencesUpdate` Pydantic model ; the response
#              is `UserPreferencesResponse` which carries the
#              EFFECTIVE values (override OR C2 default) plus the
#              `user_prompt_is_default` flag the UI uses to render
#              a 'Reset to default' affordance.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from ay_platform_core.c2_auth.models import (
    UserPreferencesResponse,
    UserPreferencesUpdate,
)
from ay_platform_core.c2_auth.service import AuthService, get_service

router = APIRouter(tags=["preferences"])


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _reject_tenant_manager(x_user_roles: str | None) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if "tenant_manager" in roles and "admin" not in roles and "tenant_admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_manager has no access to tenant content (E-100-002 v2)",
        )


@router.get("", response_model=UserPreferencesResponse)
async def get_my_preferences(
    actor_id: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> UserPreferencesResponse:
    """Read effective preferences for the calling user. Always
    succeeds — the response falls back to the C2 default user prompt
    when no override has been stored yet."""
    _reject_tenant_manager(x_user_roles)
    return await service.get_user_preferences(actor_id)


@router.put("", response_model=UserPreferencesResponse)
async def put_my_preferences(
    body: UserPreferencesUpdate,
    actor_id: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: AuthService = Depends(get_service),
) -> UserPreferencesResponse:
    """Upsert the caller's preferences. Empty-string field values
    clear the corresponding override (revert to C2 default) ; missing
    or `null` values leave the stored value untouched."""
    _reject_tenant_manager(x_user_roles)
    return await service.update_user_preferences(actor_id, body)
