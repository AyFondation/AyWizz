# =============================================================================
# File: none_mode.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/modes/none_mode.py
# Description: "none" authentication mode. Issues JWT for a single system
#              user without credential verification. Development only.
#
# @relation implements:R-100-031
# @relation implements:R-100-032
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.models import LoginRequest, RBACGlobalRole, UserPublic, UserStatus
from ay_platform_core.c2_auth.modes.base import AuthMode

SYSTEM_USER_ID = "user-john-doe"
SYSTEM_TENANT_ID = "tenant-default"
_FORBIDDEN_ENVIRONMENTS = {"production", "staging"}


class NoneMode(AuthMode):
    """Issues JWT for john.doe without credential verification.

    All sessions share the same user identity. Intended for demos and
    local single-developer use only. Forbidden in production/staging.

    @relation implements:R-100-031
    @relation implements:R-100-032
    """

    def __init__(self, config: AuthConfig) -> None:
        self._config = config

    async def authenticate(self, request: LoginRequest) -> UserPublic:
        # Guard: raise at request time as a safety net (startup already checks).
        # R-100-032: refuse operation in production/staging.
        if self._config.platform_environment in _FORBIDDEN_ENVIRONMENTS:
            raise RuntimeError(
                f"Auth mode 'none' is forbidden in "
                f"'{self._config.platform_environment}' environment. "
                "Set AUTH_MODE=local or AUTH_MODE=sso."
            )
        return UserPublic(
            user_id=SYSTEM_USER_ID,
            username="john.doe",
            tenant_id=SYSTEM_TENANT_ID,
            roles=[RBACGlobalRole.USER],
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
            name="John Doe",
            email=None,  # Absent in none mode per E-100-001
        )
