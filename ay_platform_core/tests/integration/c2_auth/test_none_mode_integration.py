# =============================================================================
# File: test_none_mode_integration.py
# Version: 3
# Path: ay_platform_core/tests/integration/c2_auth/test_none_mode_integration.py
# Description: Integration tests for none-mode auth flow (no ArangoDB).
#              Tests the full HTTP stack: router → service → NoneMode.
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from ay_platform_core.c2_auth.models import LoginRequest
from ay_platform_core.c2_auth.service import AuthService


@pytest.mark.integration
class TestNoneModeHTTP:
    async def test_get_config_returns_none_mode(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/config")
        assert resp.status_code == 200
        assert resp.json()["auth_mode"] == "none"

    async def test_login_returns_bearer_token(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/auth/login", json={"username": "x", "password": "y"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["expires_in"] == 3600

    async def test_token_grant_form_endpoint(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/auth/token",
                data={"grant_type": "password", "username": "x", "password": "y"},
            )
        assert resp.status_code == 200
        assert resp.json()["token_type"] == "bearer"

    async def test_verify_valid_token(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post("/auth/login", json={"username": "x", "password": "y"})
            token = login.json()["access_token"]

            verify = await client.get(
                "/auth/verify", headers={"Authorization": f"Bearer {token}"}
            )
        assert verify.status_code == 200
        claims = verify.json()
        assert claims["iss"] == "platform-auth"
        assert claims["aud"] == "platform"
        assert claims["auth_mode"] == "none"

    async def test_verify_without_token_returns_401(self, none_app: httpx.ASGITransport) -> None:
        # FastAPI 0.136+ HTTPBearer returns 401 (correct per RFC 7235).
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/auth/verify")
        assert resp.status_code == 401

    async def test_verify_with_invalid_token_returns_401(
        self, none_app: httpx.ASGITransport
    ) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/auth/verify", headers={"Authorization": "Bearer not.a.valid.token"}
            )
        assert resp.status_code == 401

    async def test_logout_returns_204(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post("/auth/login", json={"username": "x", "password": "y"})
            token = login.json()["access_token"]
            resp = await client.post(
                "/auth/logout", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 204


@pytest.mark.integration
class TestVerifyForwardAuthHeaders:
    """Verify that GET /auth/verify emits the three headers consumed by Traefik forward-auth."""

    async def test_verify_emits_x_user_id(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post("/auth/login", json={"username": "x", "password": "y"})
            token = login.json()["access_token"]
            verify = await client.get(
                "/auth/verify", headers={"Authorization": f"Bearer {token}"}
            )
        assert verify.status_code == 200
        assert "x-user-id" in verify.headers
        assert verify.headers["x-user-id"] == verify.json()["sub"]

    async def test_verify_emits_x_user_roles(self, none_app: httpx.ASGITransport) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post("/auth/login", json={"username": "x", "password": "y"})
            token = login.json()["access_token"]
            verify = await client.get(
                "/auth/verify", headers={"Authorization": f"Bearer {token}"}
            )
        assert "x-user-roles" in verify.headers
        roles_header = verify.headers["x-user-roles"]
        claims_roles = verify.json()["roles"]
        assert all(r in roles_header for r in claims_roles)

    async def test_verify_emits_x_platform_auth_mode(
        self, none_app: httpx.ASGITransport
    ) -> None:
        transport = httpx.ASGITransport(app=none_app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post("/auth/login", json={"username": "x", "password": "y"})
            token = login.json()["access_token"]
            verify = await client.get(
                "/auth/verify", headers={"Authorization": f"Bearer {token}"}
            )
        assert "x-platform-auth-mode" in verify.headers
        assert verify.headers["x-platform-auth-mode"] == "none"


@pytest.mark.integration
class TestNoneModeService:
    """Direct service-level integration (no HTTP overhead)."""

    async def test_full_issue_verify_cycle(self, auth_service_none: AuthService) -> None:
        req = LoginRequest(username="x", password="y")
        token_resp = await auth_service_none.issue_token(req)
        claims = await auth_service_none.verify_token(token_resp.access_token)
        assert claims.iss == "platform-auth"
        assert claims.aud == "platform"

    async def test_unique_jti_per_issuance(self, auth_service_none: AuthService) -> None:
        req = LoginRequest(username="x", password="y")
        r1 = await auth_service_none.issue_token(req)
        r2 = await auth_service_none.issue_token(req)
        c1 = await auth_service_none.verify_token(r1.access_token)
        c2 = await auth_service_none.verify_token(r2.access_token)
        assert c1.jti != c2.jti
