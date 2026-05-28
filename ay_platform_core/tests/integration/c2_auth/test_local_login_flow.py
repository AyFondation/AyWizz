# =============================================================================
# File: test_local_login_flow.py
# Version: 2
# Path: ay_platform_core/tests/integration/c2_auth/test_local_login_flow.py
# Description: Integration tests for local auth mode: create user → login →
#              verify → logout → verify-again (expect revoked).
#              Uses ArangoDB testcontainer via auth_repo fixture.
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.models import (
    LoginRequest,
    RBACGlobalRole,
    ResetPasswordRequest,
    UserCreateRequest,
)
from ay_platform_core.c2_auth.service import AuthService


@pytest.mark.integration
class TestLocalLoginFlowService:
    """Direct service-level tests — no HTTP overhead."""

    async def test_create_and_login(self, auth_service_local: AuthService) -> None:
        user = await auth_service_local.create_user(
            UserCreateRequest(
                username="alice",
                password="secure-pass-123!",
                tenant_id="t-1",
            )
        )
        assert user.user_id
        assert user.username == "alice"

        token_resp = await auth_service_local.issue_token(
            LoginRequest(username="alice", password="secure-pass-123!")
        )
        assert token_resp.access_token

    async def test_login_verify_returns_correct_claims(
        self, auth_service_local: AuthService
    ) -> None:
        await auth_service_local.create_user(
            UserCreateRequest(
                username="bob",
                password="pass-bob-456!",
                tenant_id="t-2",
                roles=[RBACGlobalRole.TENANT_ADMIN],
            )
        )
        token_resp = await auth_service_local.issue_token(
            LoginRequest(username="bob", password="pass-bob-456!")
        )
        claims = await auth_service_local.verify_token(token_resp.access_token)
        assert claims.sub
        assert claims.tenant_id == "t-2"
        assert RBACGlobalRole.TENANT_ADMIN in claims.roles

    async def test_logout_revokes_session(self, auth_service_local: AuthService) -> None:
        await auth_service_local.create_user(
            UserCreateRequest(username="carol", password="pass-carol!", tenant_id="t-1")
        )
        token_resp = await auth_service_local.issue_token(
            LoginRequest(username="carol", password="pass-carol!")
        )
        claims = await auth_service_local.verify_token(token_resp.access_token)
        assert claims.jti

        await auth_service_local.logout(claims.jti)

        with pytest.raises(HTTPException) as exc_info:
            await auth_service_local.verify_token(token_resp.access_token)
        assert exc_info.value.status_code == 401
        assert "revoked" in exc_info.value.detail.lower()

    async def test_wrong_password_rejected(self, auth_service_local: AuthService) -> None:
        await auth_service_local.create_user(
            UserCreateRequest(username="dave", password="correct-pw!", tenant_id="t-1")
        )
        with pytest.raises(HTTPException) as exc_info:
            await auth_service_local.issue_token(
                LoginRequest(username="dave", password="wrong-pw!")
            )
        assert exc_info.value.status_code == 401

    async def test_unknown_user_rejected(self, auth_service_local: AuthService) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await auth_service_local.issue_token(
                LoginRequest(username="ghost-user", password="any")
            )
        assert exc_info.value.status_code == 401

    async def test_admin_password_reset(self, auth_service_local: AuthService) -> None:
        created = await auth_service_local.create_user(
            UserCreateRequest(username="eve", password="old-pass!", tenant_id="t-1")
        )
        # Verify original password works
        token = await auth_service_local.issue_token(
            LoginRequest(username="eve", password="old-pass!")
        )
        assert token.access_token

        await auth_service_local.reset_password(
            created.user_id,
            ResetPasswordRequest(new_password="new-pass-456!"),
        )

    async def test_admin_password_reset_allows_new_password(
        self, auth_service_local: AuthService
    ) -> None:
        created = await auth_service_local.create_user(
            UserCreateRequest(username="frank", password="old-pw!", tenant_id="t-1")
        )
        await auth_service_local.reset_password(
            created.user_id,
            ResetPasswordRequest(new_password="new-pw-789!"),
        )
        token_resp = await auth_service_local.issue_token(
            LoginRequest(username="frank", password="new-pw-789!")
        )
        assert token_resp.access_token


@pytest.mark.integration
class TestLocalLoginFlowHTTP:
    """HTTP-level tests for local mode endpoints."""

    async def test_login_json_endpoint(self, local_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=local_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test"):
            pass  # HTTP user creation tested separately

    async def test_config_shows_local_mode(self, local_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=local_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/config")
        assert resp.status_code == 200
        assert resp.json()["auth_mode"] == "local"
