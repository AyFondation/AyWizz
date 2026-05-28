# =============================================================================
# File: local_mode.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c2_auth/modes/local_mode.py
# Description: "local" authentication mode. Credentials stored as
#              argon2id hashes in ArangoDB. Argon2id is the only permitted
#              hash algorithm. R-100-034.
#
# @relation implements:R-100-034
# @relation implements:R-100-035
# @relation implements:R-100-039
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import argon2
from argon2 import PasswordHasher
from fastapi import HTTPException, status

from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.models import LoginRequest, UserPublic, UserStatus
from ay_platform_core.c2_auth.modes.base import AuthMode

_ph = PasswordHasher()

# R-100-039: 5 consecutive failures → 15-minute lock.
MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


class LocalMode(AuthMode):
    """Local credential authentication with argon2id and ArangoDB storage.

    Password hashing uses argon2-cffi directly (passlib not used —
    passlib is in maintenance mode). Bcrypt, scrypt, SHA256, PBKDF2,
    and plaintext are explicitly forbidden by R-100-034.

    Account lock is stored in ArangoDB for restart-resilience and
    compatibility with horizontal scaling.

    @relation implements:R-100-034
    @relation implements:R-100-039
    """

    def __init__(self, repo: AuthRepository) -> None:
        self._repo = repo

    async def authenticate(self, request: LoginRequest) -> UserPublic:
        user = await self._repo.get_user_by_username(request.username)

        if user is None:
            # Constant-time: hash a dummy value to prevent username enumeration.
            _ph.hash("dummy-timing-safety-placeholder")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )

        if user.status == UserStatus.DISABLED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account disabled",
            )

        now = datetime.now(UTC)
        if user.locked_until is not None and user.locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Account locked until {user.locked_until.isoformat()}",
            )

        try:
            _ph.verify(user.argon2id_hash, request.password)
        except argon2.exceptions.VerifyMismatchError:
            new_count = await self._repo.increment_failed_attempts(user.user_id)
            if new_count >= MAX_FAILED_ATTEMPTS:
                locked_until = datetime.now(UTC) + timedelta(minutes=LOCK_DURATION_MINUTES)
                await self._repo.lock_user(user.user_id, locked_until)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            ) from None

        await self._repo.reset_failed_attempts(user.user_id)
        return UserPublic.model_validate(user.model_dump())

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password with argon2id. Only permitted algorithm per R-100-034."""
        return _ph.hash(password)

    @staticmethod
    def verify_password(hash_: str, password: str) -> bool:
        """Verify password against argon2id hash. Returns False on mismatch."""
        try:
            _ph.verify(hash_, password)
            return True
        except argon2.exceptions.VerifyMismatchError:
            return False
