# =============================================================================
# File: ux_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/ux_router.py
# Description: Public `/ux/config` endpoint hosted by C2 (which already
#              owns the platform's public auth surface). Returns the
#              bootstrap configuration the Next.js frontend fetches at
#              startup so it can self-configure WITHOUT a rebuild —
#              brand, feature flags, auth mode all come from C2 env
#              vars (`C2_UX_*`).
#
#              v1 keeps the response intentionally small : only what
#              the UX shell needs to render before login. Component-
#              specific data (LLM models, project list, etc.) is
#              fetched lazily from each component as the user
#              navigates.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends

from ay_platform_core.c2_auth.models import UXConfigResponse
from ay_platform_core.c2_auth.service import AuthService, get_service

ux_router = APIRouter(tags=["ux"])


@ux_router.api_route(
    "/config", methods=["GET", "HEAD"], response_model=UXConfigResponse,
)
async def get_ux_config(
    service: AuthService = Depends(get_service),
) -> UXConfigResponse:
    """Bootstrap config for the UX. Public — no authentication.

    Composed from `AuthConfig.ux_*` env-tunable fields. The UX merges
    this with the static `runtime-config.json` (deployment-time API
    URL) before first render.
    """
    return service.ux_config_response()
