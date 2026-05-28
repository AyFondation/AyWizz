# =============================================================================
# File: test_jwt_issuance.py
# Version: 2
# Path: ay_platform_core/tests/unit/c2_auth/test_jwt_issuance.py
# Description: Unit tests for AuthService.issue_token() across all auth modes.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.models import (
    JWTClaims,
    LoginRequest,
    RBACGlobalRole,
    UserInternal,
    UserStatus,
)
from ay_platform_core.c2_auth.modes.local_mode import LocalMode
from ay_platform_core.c2_auth.modes.none_mode import SYSTEM_USER_ID
from ay_platform_core.c2_auth.service import AuthService

_SECRET = "test-secret-key-32-chars-minimum!"
_REQUEST = LoginRequest(username="user", password="pass")


def _make_service(auth_mode: str = "none", repo: object = None) -> AuthService:
    config = AuthConfig.model_validate({
        "auth_mode": auth_mode,
        "jwt_secret_key": _SECRET,
        "platform_environment": "testing",
    })
    return AuthService(config, repo)  # type: ignore[arg-type]


def _make_local_repo(password: str = "correct") -> AsyncMock:
    repo = AsyncMock()
    user = UserInternal(
        user_id="u-1",
        username="user",
        tenant_id="t-1",
        roles=[RBACGlobalRole.USER],
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        argon2id_hash=LocalMode.hash_password(password),
    )
    repo.get_user_by_username.return_value = user
    repo.reset_failed_attempts.return_value = None
    repo.insert_session.return_value = None
    repo.get_project_scopes.return_value = {}
    return repo


@pytest.mark.unit
class TestIssueTokenNoneMode:
    async def test_returns_token_response(self) -> None:
        service = _make_service("none")
        resp = await service.issue_token(_REQUEST)
        assert resp.access_token
        assert resp.token_type == "bearer"
        assert resp.expires_in == 3600

    async def test_jwt_claims_match_e100001(self) -> None:
        service = _make_service("none")
        resp = await service.issue_token(_REQUEST)
        payload = jwt.decode(
            resp.access_token, _SECRET, algorithms=["HS256"], audience="platform"
        )
        claims = JWTClaims(**payload)
        assert claims.iss == "platform-auth"
        assert claims.aud == "platform"
        assert claims.auth_mode == "none"
        assert claims.tenant_id
        assert claims.jti
        assert claims.iat < claims.exp

    async def test_each_token_has_unique_jti(self) -> None:
        service = _make_service("none")
        r1 = await service.issue_token(_REQUEST)
        r2 = await service.issue_token(_REQUEST)
        p1 = jwt.decode(r1.access_token, _SECRET, algorithms=["HS256"], audience="platform")
        p2 = jwt.decode(r2.access_token, _SECRET, algorithms=["HS256"], audience="platform")
        assert p1["jti"] != p2["jti"]

    async def test_sub_is_system_user(self) -> None:
        service = _make_service("none")
        resp = await service.issue_token(_REQUEST)
        payload = jwt.decode(
            resp.access_token, _SECRET, algorithms=["HS256"], audience="platform"
        )
        assert payload["sub"] == SYSTEM_USER_ID

    async def test_email_absent_in_none_mode(self) -> None:
        service = _make_service("none")
        resp = await service.issue_token(_REQUEST)
        payload = jwt.decode(
            resp.access_token, _SECRET, algorithms=["HS256"], audience="platform"
        )
        assert payload.get("email") is None


@pytest.mark.unit
class TestIssueTokenLocalMode:
    async def test_returns_token_response(self) -> None:
        repo = _make_local_repo("correct")
        service = _make_service("local", repo)
        resp = await service.issue_token(LoginRequest(username="user", password="correct"))
        assert resp.access_token

    async def test_session_inserted(self) -> None:
        repo = _make_local_repo("correct")
        service = _make_service("local", repo)
        await service.issue_token(LoginRequest(username="user", password="correct"))
        repo.insert_session.assert_awaited_once()

    async def test_auth_mode_claim_is_local(self) -> None:
        repo = _make_local_repo("correct")
        service = _make_service("local", repo)
        resp = await service.issue_token(LoginRequest(username="user", password="correct"))
        payload = jwt.decode(
            resp.access_token, _SECRET, algorithms=["HS256"], audience="platform"
        )
        assert payload["auth_mode"] == "local"


@pytest.mark.unit
class TestIssueTokenSSOMode:
    async def test_raises_501(self) -> None:
        service = _make_service("sso")
        with pytest.raises(HTTPException) as exc_info:
            await service.issue_token(_REQUEST)
        assert exc_info.value.status_code == 501


@pytest.mark.unit
class TestProductionGuard:
    def test_service_init_fails_in_production(self) -> None:
        """R-100-032: AuthService must not start with none mode in production."""
        config = AuthConfig.model_validate({
            "auth_mode": "none",
            "jwt_secret_key": _SECRET,
            "platform_environment": "production",
        })
        with pytest.raises(RuntimeError, match="forbidden"):
            AuthService(config)

    def test_service_init_fails_in_staging(self) -> None:
        config = AuthConfig.model_validate({
            "auth_mode": "none",
            "jwt_secret_key": _SECRET,
            "platform_environment": "staging",
        })
        with pytest.raises(RuntimeError, match="forbidden"):
            AuthService(config)
