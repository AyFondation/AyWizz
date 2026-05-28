# =============================================================================
# File: base.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/modes/base.py
# Description: Abstract base for pluggable authentication modes.
#
# @relation implements:R-100-030
# =============================================================================

from __future__ import annotations

from abc import ABC, abstractmethod

from ay_platform_core.c2_auth.models import LoginRequest, UserPublic


class AuthMode(ABC):
    """Pluggable authentication strategy. R-100-030.

    Each mode verifies credentials differently but returns a uniform
    UserPublic payload. The service layer then builds the JWT from it.

    @relation implements:R-100-030
    """

    @abstractmethod
    async def authenticate(self, request: LoginRequest) -> UserPublic:
        """Verify credentials and return authenticated user data.

        Raises:
            HTTPException 401: Invalid credentials.
            HTTPException 403: Account disabled.
            HTTPException 429: Account temporarily locked.
            HTTPException 501: Mode not yet implemented.
        """
