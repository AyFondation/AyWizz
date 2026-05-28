# =============================================================================
# File: test_account_lock.py
# Version: 1
# Path: ay_platform_core/tests/integration/c2_auth/test_account_lock.py
# Description: Integration tests for account lock mechanism (R-100-039).
#              5 consecutive failed logins → 15-minute lock.
#              Uses ArangoDB testcontainer via auth_repo fixture.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.models import (
    LoginRequest,
    UserCreateRequest,
)
from ay_platform_core.c2_auth.modes.local_mode import LOCK_DURATION_MINUTES, MAX_FAILED_ATTEMPTS
from ay_platform_core.c2_auth.service import AuthService


@pytest.mark.integration
class TestAccountLock:
    """R-100-039: 5 failed logins per account → 15-minute lock in local mode."""

    async def _create_user(
        self, service: AuthService, username: str = "locktest"
    ) -> str:
        user = await service.create_user(
            UserCreateRequest(username=username, password="correct-pw!", tenant_id="t-1")
        )
        return user.user_id

    async def test_single_failure_increments_counter(
        self, auth_service_local: AuthService, auth_repo: AuthRepository
    ) -> None:
        user_id = await self._create_user(auth_service_local, "counter-user")

        with pytest.raises(HTTPException):
            await auth_service_local.issue_token(
                LoginRequest(username="counter-user", password="wrong")
            )

        user = await auth_repo.get_user_by_id(user_id)
        assert user is not None
        assert user.failed_attempts == 1

    async def test_successful_login_resets_counter(
        self, auth_service_local: AuthService, auth_repo: AuthRepository
    ) -> None:
        user_id = await self._create_user(auth_service_local, "reset-user")

        # One failure
        with pytest.raises(HTTPException):
            await auth_service_local.issue_token(
                LoginRequest(username="reset-user", password="wrong")
            )

        # Successful login
        await auth_service_local.issue_token(
            LoginRequest(username="reset-user", password="correct-pw!")
        )

        user = await auth_repo.get_user_by_id(user_id)
        assert user is not None
        assert user.failed_attempts == 0

    async def test_max_failures_triggers_lock(
        self, auth_service_local: AuthService, auth_repo: AuthRepository
    ) -> None:
        user_id = await self._create_user(auth_service_local, "lock-user")

        for _ in range(MAX_FAILED_ATTEMPTS):
            with pytest.raises(HTTPException):
                await auth_service_local.issue_token(
                    LoginRequest(username="lock-user", password="wrong")
                )

        user = await auth_repo.get_user_by_id(user_id)
        assert user is not None
        assert user.locked_until is not None
        expected_min = datetime.now(UTC) + timedelta(minutes=LOCK_DURATION_MINUTES - 1)
        assert user.locked_until > expected_min

    async def test_locked_account_rejects_correct_password(
        self, auth_service_local: AuthService, auth_repo: AuthRepository
    ) -> None:
        await self._create_user(auth_service_local, "locked-correct-user")

        # Trigger the lock
        for _ in range(MAX_FAILED_ATTEMPTS):
            with pytest.raises(HTTPException):
                await auth_service_local.issue_token(
                    LoginRequest(username="locked-correct-user", password="wrong")
                )

        # Even correct password is rejected while locked
        with pytest.raises(HTTPException) as exc_info:
            await auth_service_local.issue_token(
                LoginRequest(username="locked-correct-user", password="correct-pw!")
            )
        assert exc_info.value.status_code == 429

    async def test_manual_unlock_allows_login(
        self, auth_service_local: AuthService, auth_repo: AuthRepository
    ) -> None:
        """Simulates unlock by manually setting locked_until to the past."""
        user_id = await self._create_user(auth_service_local, "unlock-user")

        # Trigger lock
        for _ in range(MAX_FAILED_ATTEMPTS):
            with pytest.raises(HTTPException):
                await auth_service_local.issue_token(
                    LoginRequest(username="unlock-user", password="wrong")
                )

        # Manually expire the lock (simulate time passing)
        past = datetime.now(UTC) - timedelta(minutes=1)
        await auth_repo.lock_user(user_id, past)
        await auth_repo.update_user(user_id, {"failed_attempts": 0})

        # Login should now succeed
        token = await auth_service_local.issue_token(
            LoginRequest(username="unlock-user", password="correct-pw!")
        )
        assert token.access_token

    async def test_below_threshold_no_lock(
        self, auth_service_local: AuthService, auth_repo: AuthRepository
    ) -> None:
        user_id = await self._create_user(auth_service_local, "below-threshold-user")

        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            with pytest.raises(HTTPException):
                await auth_service_local.issue_token(
                    LoginRequest(username="below-threshold-user", password="wrong")
                )

        user = await auth_repo.get_user_by_id(user_id)
        assert user is not None
        assert user.locked_until is None
        assert user.failed_attempts == MAX_FAILED_ATTEMPTS - 1
