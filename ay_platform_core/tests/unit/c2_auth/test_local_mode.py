# =============================================================================
# File: test_local_mode.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_local_mode.py
# Description: Unit tests for LocalMode. ArangoDB is mocked via FakeRepository.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.models import (
    LoginRequest,
    RBACGlobalRole,
    UserInternal,
    UserStatus,
)
from ay_platform_core.c2_auth.modes.local_mode import (
    LOCK_DURATION_MINUTES,
    MAX_FAILED_ATTEMPTS,
    LocalMode,
)


def _make_user(
    failed_attempts: int = 0,
    locked_until: datetime | None = None,
    status: UserStatus = UserStatus.ACTIVE,
    password: str = "correct-password",
) -> UserInternal:
    return UserInternal(
        user_id="user-test-id",
        username="testuser",
        tenant_id="tenant-1",
        roles=[RBACGlobalRole.USER],
        status=status,
        created_at=datetime.now(UTC),
        argon2id_hash=LocalMode.hash_password(password),
        failed_attempts=failed_attempts,
        locked_until=locked_until,
    )


def _make_mock_repo(user: UserInternal | None = None) -> Any:
    repo = AsyncMock()
    repo.get_user_by_username.return_value = user
    repo.increment_failed_attempts.return_value = 1
    repo.reset_failed_attempts.return_value = None
    repo.lock_user.return_value = None
    return repo


@pytest.mark.unit
class TestLocalModeHashPassword:
    def test_produces_argon2id_hash(self) -> None:
        hash_ = LocalMode.hash_password("my-password")
        assert hash_.startswith("$argon2id$")

    def test_different_calls_produce_different_hashes(self) -> None:
        h1 = LocalMode.hash_password("same")
        h2 = LocalMode.hash_password("same")
        assert h1 != h2  # argon2id salts are random

    def test_verify_correct_password(self) -> None:
        h = LocalMode.hash_password("correct")
        assert LocalMode.verify_password(h, "correct") is True

    def test_verify_wrong_password(self) -> None:
        h = LocalMode.hash_password("correct")
        assert LocalMode.verify_password(h, "wrong") is False


@pytest.mark.unit
class TestLocalModeAuthenticate:
    async def test_valid_credentials_return_user(self) -> None:
        user = _make_user(password="secret")
        repo = _make_mock_repo(user=user)
        mode = LocalMode(repo)
        result = await mode.authenticate(LoginRequest(username="testuser", password="secret"))
        assert result.user_id == user.user_id
        repo.reset_failed_attempts.assert_awaited_once_with(user.user_id)

    async def test_unknown_user_raises_401(self) -> None:
        repo = _make_mock_repo(user=None)
        mode = LocalMode(repo)
        with pytest.raises(HTTPException) as exc_info:
            await mode.authenticate(LoginRequest(username="ghost", password="pw"))
        assert exc_info.value.status_code == 401

    async def test_wrong_password_raises_401(self) -> None:
        user = _make_user(password="correct")
        repo = _make_mock_repo(user=user)
        mode = LocalMode(repo)
        with pytest.raises(HTTPException) as exc_info:
            await mode.authenticate(LoginRequest(username="testuser", password="wrong"))
        assert exc_info.value.status_code == 401

    async def test_disabled_account_raises_403(self) -> None:
        user = _make_user(status=UserStatus.DISABLED)
        repo = _make_mock_repo(user=user)
        mode = LocalMode(repo)
        with pytest.raises(HTTPException) as exc_info:
            await mode.authenticate(LoginRequest(username="testuser", password="correct-password"))
        assert exc_info.value.status_code == 403

    async def test_locked_account_raises_429(self) -> None:
        locked_until = datetime.now(UTC) + timedelta(minutes=10)
        user = _make_user(locked_until=locked_until)
        repo = _make_mock_repo(user=user)
        mode = LocalMode(repo)
        with pytest.raises(HTTPException) as exc_info:
            await mode.authenticate(LoginRequest(username="testuser", password="correct-password"))
        assert exc_info.value.status_code == 429

    async def test_expired_lock_allows_login(self) -> None:
        past = datetime.now(UTC) - timedelta(minutes=1)
        user = _make_user(locked_until=past, password="secret")
        repo = _make_mock_repo(user=user)
        mode = LocalMode(repo)
        result = await mode.authenticate(LoginRequest(username="testuser", password="secret"))
        assert result.user_id == user.user_id

    async def test_failed_attempt_triggers_increment(self) -> None:
        user = _make_user(password="correct")
        repo = _make_mock_repo(user=user)
        repo.increment_failed_attempts.return_value = 1
        mode = LocalMode(repo)
        with pytest.raises(HTTPException):
            await mode.authenticate(LoginRequest(username="testuser", password="wrong"))
        repo.increment_failed_attempts.assert_awaited_once_with(user.user_id)

    async def test_max_failures_triggers_lock(self) -> None:
        user = _make_user(password="correct")
        repo = _make_mock_repo(user=user)
        repo.increment_failed_attempts.return_value = MAX_FAILED_ATTEMPTS
        mode = LocalMode(repo)
        with pytest.raises(HTTPException):
            await mode.authenticate(LoginRequest(username="testuser", password="wrong"))
        repo.lock_user.assert_awaited_once()
        locked_at = repo.lock_user.call_args[0][1]
        expected_min = datetime.now(UTC) + timedelta(minutes=LOCK_DURATION_MINUTES - 1)
        assert locked_at > expected_min

    async def test_below_max_failures_no_lock(self) -> None:
        user = _make_user(password="correct")
        repo = _make_mock_repo(user=user)
        repo.increment_failed_attempts.return_value = MAX_FAILED_ATTEMPTS - 1
        mode = LocalMode(repo)
        with pytest.raises(HTTPException):
            await mode.authenticate(LoginRequest(username="testuser", password="wrong"))
        repo.lock_user.assert_not_awaited()
