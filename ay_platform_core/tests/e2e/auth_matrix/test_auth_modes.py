# =============================================================================
# File: test_auth_modes.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/test_auth_modes.py
# Description: Authentication-mode coverage at the C2 boundary.
#              Three modes per R-100-030..037:
#
#              - `local`: username/password → JWT issued by C2.
#              - `none`: bypass for development; SHALL fail-fast at
#                startup when PLATFORM_ENVIRONMENT in {production, staging}
#                (R-100-032).
#              - `sso`: stub (501) until oauth2-proxy (variant A) is
#                deployed. Future full-flow test will use a mock JWKS;
#                kept minimal here — only the 501 contract is asserted.
#                When the real SSO mode lands, this file SHALL switch
#                to a mock-JWKS round-trip and assert the issued claims
#                propagate through forward-auth identically to local mode.
#
# @relation validates:E-100-001
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.models import RBACGlobalRole, UserCreateRequest
from ay_platform_core.c2_auth.router import router as c2_router
from ay_platform_core.c2_auth.service import AuthService
from ay_platform_core.c2_auth.service import get_service as c2_get_service
from tests.e2e.auth_matrix._stack import PlatformStack

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ---------------------------------------------------------------------------
# Helpers — per-mode AuthService factories
# ---------------------------------------------------------------------------


def _build_c2_app(
    *,
    auth_mode: str,
    platform_environment: str,
    arango_url: str,
    arango_password: str,
    db_name: str,
) -> tuple[FastAPI, AuthService]:
    """Build a C2 FastAPI app configured for `auth_mode`. Reuses the
    auth_matrix Arango container; each call uses its own DB to keep
    user/session state isolated between modes."""
    repo = AuthRepository.from_config(arango_url, db_name, "root", arango_password)
    repo._ensure_collections_sync()
    config = AuthConfig.model_validate(
        {
            "auth_mode": auth_mode,
            "jwt_secret_key": "auth-matrix-mode-test-32-chars-min!",
            "platform_environment": platform_environment,
        }
    )
    service = AuthService(config, repo)
    app = FastAPI()
    app.include_router(c2_router, prefix="/auth")
    app.dependency_overrides[c2_get_service] = lambda: service
    return app, service


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-mode",
    )


# ---------------------------------------------------------------------------
# `local` mode — full login round-trip
# ---------------------------------------------------------------------------


async def test_local_mode_login_issues_jwt(
    auth_matrix_stack: PlatformStack,
) -> None:
    """In local mode, POST /auth/login with valid credentials returns a
    JWT; that JWT verifies against /auth/verify and decodes to claims
    matching the seeded user's identity."""
    db_name = f"e2e_authmodes_local_{uuid.uuid4().hex[:6]}"
    auth_matrix_stack.arango_client.db(
        "_system", username="root", password=auth_matrix_stack.arango_password,
    ).create_database(db_name)
    try:
        app, service = _build_c2_app(
            auth_mode="local",
            platform_environment="testing",
            arango_url=f"http://{auth_matrix_stack.arango_client.hosts[0].split('://', 1)[-1]}",
            arango_password=auth_matrix_stack.arango_password,
            db_name=db_name,
        )
        username = f"local-{uuid.uuid4().hex[:6]}@auth-matrix.test"
        password = "LocalModeTest1!"
        await service.create_user(
            UserCreateRequest(
                username=username,
                password=password,
                tenant_id="tenant-modes",
                roles=[RBACGlobalRole.USER],
            )
        )

        async with _client(app) as c:
            login = await c.post(
                "/auth/login", json={"username": username, "password": password}
            )
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]
            assert isinstance(token, str) and token.count(".") == 2

            verify = await c.get(
                "/auth/verify", headers={"Authorization": f"Bearer {token}"}
            )
            assert verify.status_code == 200, verify.text
            claims = verify.json()
            assert claims["auth_mode"] == "local"
            assert claims["sub"]
            assert "user" in claims["roles"]
    finally:
        auth_matrix_stack.arango_client.db(
            "_system", username="root", password=auth_matrix_stack.arango_password,
        ).delete_database(db_name)


async def test_local_mode_wrong_password_returns_401(
    auth_matrix_stack: PlatformStack,
) -> None:
    db_name = f"e2e_authmodes_localwp_{uuid.uuid4().hex[:6]}"
    auth_matrix_stack.arango_client.db(
        "_system", username="root", password=auth_matrix_stack.arango_password,
    ).create_database(db_name)
    try:
        app, service = _build_c2_app(
            auth_mode="local",
            platform_environment="testing",
            arango_url=f"http://{auth_matrix_stack.arango_client.hosts[0].split('://', 1)[-1]}",
            arango_password=auth_matrix_stack.arango_password,
            db_name=db_name,
        )
        username = f"localwp-{uuid.uuid4().hex[:6]}@auth-matrix.test"
        await service.create_user(
            UserCreateRequest(
                username=username,
                password="CorrectPw1!",
                tenant_id="tenant-modes",
                roles=[RBACGlobalRole.USER],
            )
        )
        async with _client(app) as c:
            response = await c.post(
                "/auth/login",
                json={"username": username, "password": "WrongPw1!"},
            )
        assert response.status_code == 401
    finally:
        auth_matrix_stack.arango_client.db(
            "_system", username="root", password=auth_matrix_stack.arango_password,
        ).delete_database(db_name)


# ---------------------------------------------------------------------------
# `none` mode — startup guard (R-100-032)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden_env", ["production", "staging"])
async def test_none_mode_refused_in_production_environments(
    auth_matrix_stack: PlatformStack, forbidden_env: str
) -> None:
    """R-100-032: AuthService SHALL refuse to start when auth_mode=none
    AND platform_environment in {staging, production}."""
    db_name = f"e2e_authmodes_none_{uuid.uuid4().hex[:6]}"
    auth_matrix_stack.arango_client.db(
        "_system", username="root", password=auth_matrix_stack.arango_password,
    ).create_database(db_name)
    try:
        repo = AuthRepository.from_config(
            f"http://{auth_matrix_stack.arango_client.hosts[0].split('://', 1)[-1]}",
            db_name,
            "root",
            auth_matrix_stack.arango_password,
        )
        config = AuthConfig.model_validate(
            {
                "auth_mode": "none",
                "jwt_secret_key": "auth-matrix-mode-test-32-chars-min!",
                "platform_environment": forbidden_env,
            }
        )
        with pytest.raises(RuntimeError, match=forbidden_env):
            AuthService(config, repo)
    finally:
        auth_matrix_stack.arango_client.db(
            "_system", username="root", password=auth_matrix_stack.arango_password,
        ).delete_database(db_name)


# ---------------------------------------------------------------------------
# `sso` mode — current contract is 501 (stub)
# ---------------------------------------------------------------------------


async def test_sso_mode_login_returns_501(
    auth_matrix_stack: PlatformStack,
) -> None:
    """SSO mode is a stub until oauth2-proxy (variant A) lands. The
    contract until then: POST /auth/login SHALL return 501. When the
    real SSO mode ships, this test SHALL be replaced by a mock-JWKS
    round-trip asserting that an externally-issued JWT is accepted
    and propagated identically to local mode."""
    db_name = f"e2e_authmodes_sso_{uuid.uuid4().hex[:6]}"
    auth_matrix_stack.arango_client.db(
        "_system", username="root", password=auth_matrix_stack.arango_password,
    ).create_database(db_name)
    try:
        app, _service = _build_c2_app(
            auth_mode="sso",
            platform_environment="testing",
            arango_url=f"http://{auth_matrix_stack.arango_client.hosts[0].split('://', 1)[-1]}",
            arango_password=auth_matrix_stack.arango_password,
            db_name=db_name,
        )
        async with _client(app) as c:
            response = await c.post(
                "/auth/login", json={"username": "x", "password": "y"}
            )
        assert response.status_code == 501
        assert "SSO" in response.json().get("detail", "")
    finally:
        auth_matrix_stack.arango_client.db(
            "_system", username="root", password=auth_matrix_stack.arango_password,
        ).delete_database(db_name)
