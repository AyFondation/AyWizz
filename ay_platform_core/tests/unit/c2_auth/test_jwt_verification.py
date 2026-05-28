# =============================================================================
# File: test_jwt_verification.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_jwt_verification.py
# Description: Unit tests for AuthService.verify_token().
# =============================================================================

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.models import LoginRequest
from ay_platform_core.c2_auth.service import AuthService

_SECRET = "test-secret-key-32-chars-minimum!"
_REQUEST = LoginRequest(username="u", password="p")


def _make_service(repo: object = None) -> AuthService:
    config = AuthConfig.model_validate({
        "auth_mode": "none",
        "jwt_secret_key": _SECRET,
        "platform_environment": "testing",
    })
    return AuthService(config, repo)  # type: ignore[arg-type]


def _make_raw_token(overrides: dict[str, Any] | None = None, secret: str = _SECRET) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "platform-auth",
        "sub": "user-john-doe",
        "aud": "platform",
        "iat": now,
        "exp": now + 3600,
        "jti": "test-jti",
        "auth_mode": "none",
        "tenant_id": "tenant-default",
        "roles": ["user"],
        "project_scopes": {},
        "name": "John Doe",
        "email": None,
    }
    if overrides:
        payload.update(overrides)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.unit
class TestVerifyTokenStateless:
    """verify_token() with no repository (none mode, stateless)."""

    async def test_valid_token_returns_claims(self) -> None:
        service = _make_service()
        token = _make_raw_token()
        claims = await service.verify_token(token)
        assert claims.sub == "user-john-doe"
        assert claims.jti == "test-jti"

    async def test_expired_token_raises_401(self) -> None:
        token = _make_raw_token({"exp": int(time.time()) - 1})
        service = _make_service()
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_token(token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    async def test_wrong_signature_raises_401(self) -> None:
        token = _make_raw_token(secret="different-secret-key-32-chars!!!!")
        service = _make_service()
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_token(token)
        assert exc_info.value.status_code == 401

    async def test_wrong_audience_raises_401(self) -> None:
        token = _make_raw_token({"aud": "wrong-service"})
        service = _make_service()
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_token(token)
        assert exc_info.value.status_code == 401

    async def test_malformed_token_raises_401(self) -> None:
        service = _make_service()
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_token("this.is.not.a.jwt")
        assert exc_info.value.status_code == 401


@pytest.mark.unit
class TestVerifyTokenWithSession:
    """verify_token() with a mocked repository (session revocation)."""

    async def test_active_session_returns_claims(self) -> None:
        repo = AsyncMock()
        repo.get_session.return_value = {"_key": "test-jti", "active": True}
        service = _make_service(repo)
        token = _make_raw_token()
        claims = await service.verify_token(token)
        assert claims.jti == "test-jti"
        repo.get_session.assert_awaited_once_with("test-jti")

    async def test_missing_session_raises_401(self) -> None:
        repo = AsyncMock()
        repo.get_session.return_value = None
        service = _make_service(repo)
        token = _make_raw_token()
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_token(token)
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail.lower()

    async def test_inactive_session_raises_401(self) -> None:
        repo = AsyncMock()
        repo.get_session.return_value = {"_key": "test-jti", "active": False}
        service = _make_service(repo)
        token = _make_raw_token()
        with pytest.raises(HTTPException) as exc_info:
            await service.verify_token(token)
        assert exc_info.value.status_code == 401


@pytest.mark.unit
class TestLogout:
    async def test_logout_deactivates_session(self) -> None:
        repo = AsyncMock()
        service = _make_service(repo)
        await service.logout("some-jti")
        repo.deactivate_session.assert_awaited_once_with("some-jti")

    async def test_logout_without_repo_is_noop(self) -> None:
        service = _make_service(repo=None)
        await service.logout("some-jti")  # Must not raise
