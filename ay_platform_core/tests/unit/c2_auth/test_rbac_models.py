# =============================================================================
# File: test_rbac_models.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_rbac_models.py
# Description: Unit tests for RBAC enum and JWTClaims serialisation.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c2_auth.models import (
    JWTClaims,
    RBACGlobalRole,
    RBACProjectRole,
    UserStatus,
)


@pytest.mark.unit
class TestRBACGlobalRole:
    def test_values_match_spec(self) -> None:
        # E-100-002 v2: 4 global roles.
        assert RBACGlobalRole.TENANT_MANAGER.value == "tenant_manager"
        assert RBACGlobalRole.ADMIN.value == "admin"
        assert RBACGlobalRole.TENANT_ADMIN.value == "tenant_admin"
        assert RBACGlobalRole.USER.value == "user"

    def test_all_four_roles_defined(self) -> None:
        assert {r.value for r in RBACGlobalRole} == {
            "tenant_manager", "admin", "tenant_admin", "user",
        }


@pytest.mark.unit
class TestRBACProjectRole:
    def test_values_match_spec(self) -> None:
        assert RBACProjectRole.OWNER.value == "project_owner"
        assert RBACProjectRole.EDITOR.value == "project_editor"
        assert RBACProjectRole.VIEWER.value == "project_viewer"

    def test_all_three_roles_defined(self) -> None:
        assert {r.value for r in RBACProjectRole} == {
            "project_owner", "project_editor", "project_viewer"
        }


@pytest.mark.unit
class TestJWTClaims:
    def _make_claims(self, **overrides: object) -> JWTClaims:
        defaults: dict[str, object] = {
            "sub": "user-abc",
            "iat": 1700000000,
            "exp": 1700003600,
            "jti": "jti-xyz",
            "auth_mode": "none",
            "tenant_id": "tenant-default",
            "roles": [RBACGlobalRole.USER],
        }
        defaults.update(overrides)
        return JWTClaims(**defaults)  # type: ignore[arg-type]

    def test_fixed_fields_have_correct_values(self) -> None:
        claims = self._make_claims()
        assert claims.iss == "platform-auth"
        assert claims.aud == "platform"

    def test_project_scopes_defaults_empty(self) -> None:
        claims = self._make_claims()
        assert claims.project_scopes == {}

    def test_email_optional(self) -> None:
        claims = self._make_claims(email=None)
        assert claims.email is None
        claims_with_email = self._make_claims(email="user@example.com")
        assert claims_with_email.email == "user@example.com"

    def test_name_optional(self) -> None:
        claims = self._make_claims(name=None)
        assert claims.name is None

    def test_project_scopes_serialised_correctly(self) -> None:
        scopes = {"proj-1": [RBACProjectRole.OWNER, RBACProjectRole.VIEWER]}
        claims = self._make_claims(project_scopes=scopes)
        dumped = claims.model_dump(mode="json")
        assert dumped["project_scopes"] == {
            "proj-1": ["project_owner", "project_viewer"]
        }

    def test_roles_list_serialised_as_strings(self) -> None:
        claims = self._make_claims(roles=[RBACGlobalRole.ADMIN, RBACGlobalRole.USER])
        dumped = claims.model_dump(mode="json")
        assert dumped["roles"] == ["admin", "user"]

    def test_auth_mode_all_values_accepted(self) -> None:
        for mode in ("none", "local", "sso"):
            c = self._make_claims(auth_mode=mode)
            assert c.auth_mode == mode


@pytest.mark.unit
class TestUserStatus:
    def test_active_and_disabled(self) -> None:
        assert UserStatus.ACTIVE.value == "active"
        assert UserStatus.DISABLED.value == "disabled"
