# =============================================================================
# File: sso_mode.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/modes/sso_mode.py
# Description: "sso" authentication mode stub. Full implementation is deferred
#              until oauth2-proxy (variant A) is deployed. Issuing tokens
#              before the proxy is in place would create untestable trust logic.
#
# @relation implements:R-100-037
# =============================================================================

from __future__ import annotations

from fastapi import HTTPException, status

from ay_platform_core.c2_auth.models import LoginRequest, UserPublic
from ay_platform_core.c2_auth.modes.base import AuthMode


class SSOMode(AuthMode):
    """SSO mode stub — returns HTTP 501 until oauth2-proxy is deployed.

    Full implementation: read X-Auth-Request-* headers set by oauth2-proxy,
    look up or auto-create the user in ArangoDB, return UserPublic.
    Deferred: no integration tests possible without a running proxy.

    @relation implements:R-100-037
    """

    async def authenticate(self, request: LoginRequest) -> UserPublic:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "SSO mode is not yet available. "
                "Deploy oauth2-proxy (variant A) and re-enable this mode."
            ),
        )
