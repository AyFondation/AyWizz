# =============================================================================
# File: test_none_mode.py
# Version: 2
# Path: ay_platform_core/tests/unit/c2_auth/test_none_mode.py
# Description: Unit tests for NoneMode authentication.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.models import LoginRequest, RBACGlobalRole
from ay_platform_core.c2_auth.modes.none_mode import SYSTEM_TENANT_ID, SYSTEM_USER_ID, NoneMode


def _make_config(**overrides: object) -> AuthConfig:
    defaults: dict[str, object] = {
        "auth_mode": "none",
        "jwt_secret_key": "test-secret-key-32-chars-min!!!!",
        "platform_environment": "development",
    }
    defaults.update(overrides)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestNoneMode:
    async def test_returns_system_user(self) -> None:
        mode = NoneMode(_make_config())
        user = await mode.authenticate(LoginRequest(username="anyone", password="anything"))
        assert user.user_id == SYSTEM_USER_ID
        assert user.username == "john.doe"
        assert user.tenant_id == SYSTEM_TENANT_ID
        assert RBACGlobalRole.USER in user.roles

    async def test_ignores_credentials(self) -> None:
        mode = NoneMode(_make_config())
        user1 = await mode.authenticate(LoginRequest(username="alice", password="pw1"))
        user2 = await mode.authenticate(LoginRequest(username="bob", password="pw2"))
        assert user1.user_id == user2.user_id

    async def test_email_absent_in_none_mode(self) -> None:
        mode = NoneMode(_make_config())
        user = await mode.authenticate(LoginRequest(username="x", password="y"))
        assert user.email is None

    async def test_name_is_john_doe(self) -> None:
        mode = NoneMode(_make_config())
        user = await mode.authenticate(LoginRequest(username="x", password="y"))
        assert user.name == "John Doe"

    @pytest.mark.parametrize("env", ["production", "staging"])
    async def test_production_guard_raises(self, env: str) -> None:
        """R-100-032: none mode forbidden in production/staging."""
        mode = NoneMode(_make_config(platform_environment=env))
        with pytest.raises(RuntimeError, match="forbidden"):
            await mode.authenticate(LoginRequest(username="x", password="y"))

    async def test_development_environment_allows_system_user(self) -> None:
        """R-100-032: production guard does not trigger for development env;
        authenticate returns the SYSTEM_USER regardless of credentials."""
        mode = NoneMode(_make_config(platform_environment="development"))
        user = await mode.authenticate(LoginRequest(username="x", password="y"))
        assert user.user_id == SYSTEM_USER_ID
        assert user.tenant_id == SYSTEM_TENANT_ID

    async def test_testing_environment_allows_system_user(self) -> None:
        """R-100-032: production guard does not trigger for testing env;
        authenticate returns the SYSTEM_USER regardless of credentials."""
        mode = NoneMode(_make_config(platform_environment="testing"))
        user = await mode.authenticate(LoginRequest(username="x", password="y"))
        assert user.user_id == SYSTEM_USER_ID
        assert user.tenant_id == SYSTEM_TENANT_ID
