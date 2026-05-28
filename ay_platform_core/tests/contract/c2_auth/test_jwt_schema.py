# =============================================================================
# File: test_jwt_schema.py
# Version: 2
# Path: ay_platform_core/tests/contract/c2_auth/test_jwt_schema.py
# Description: Contract tests — JWTClaims schema matches E-100-001 exactly.
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c2_auth.models import JWTClaims


@pytest.mark.contract
class TestJWTClaimsSchema:
    """Verify every field declared in E-100-001 is present and typed correctly."""

    REQUIRED_STRING_FIELDS = ("iss", "sub", "aud", "jti", "auth_mode", "tenant_id")
    REQUIRED_INT_FIELDS = ("iat", "exp")
    OPTIONAL_STRING_FIELDS = ("name", "email")

    def _sample(self) -> JWTClaims:
        return JWTClaims(
            sub="u-1",
            iat=1700000000,
            exp=1700003600,
            jti="jti-1",
            auth_mode="none",
            tenant_id="t-1",
            roles=[],
        )

    def test_required_string_fields_present(self) -> None:
        fields = JWTClaims.model_fields
        for name in self.REQUIRED_STRING_FIELDS:
            assert name in fields, f"Missing field: {name}"

    def test_required_int_fields_present(self) -> None:
        fields = JWTClaims.model_fields
        for name in self.REQUIRED_INT_FIELDS:
            assert name in fields, f"Missing field: {name}"

    def test_optional_string_fields_present(self) -> None:
        fields = JWTClaims.model_fields
        for name in self.OPTIONAL_STRING_FIELDS:
            assert name in fields, f"Missing optional field: {name}"

    def test_iss_is_always_platform_auth(self) -> None:
        c = self._sample()
        assert c.iss == "platform-auth"

    def test_aud_is_always_platform(self) -> None:
        c = self._sample()
        assert c.aud == "platform"

    def test_roles_field_is_list(self) -> None:
        c = self._sample()
        assert isinstance(c.roles, list)

    def test_project_scopes_field_is_dict(self) -> None:
        c = self._sample()
        assert isinstance(c.project_scopes, dict)

    def test_auth_mode_rejects_unknown_value(self) -> None:
        with pytest.raises(ValidationError):
            JWTClaims(
                sub="u",
                iat=1,
                exp=2,
                jti="j",
                auth_mode="invalid-mode",  # type: ignore[arg-type]
                tenant_id="t",
                roles=[],
            )

    def test_no_extra_undeclared_fields_in_spec(self) -> None:
        """Exactly the fields from E-100-001 v2 — no silent additions.

        v2 added `username` (login name, distinct from `name` display).
        """
        expected = {
            "iss", "sub", "aud", "iat", "exp", "jti",
            "auth_mode", "tenant_id", "roles", "project_scopes",
            "username", "name", "email",
        }
        actual = set(JWTClaims.model_fields.keys())
        assert actual == expected, (
            f"Field mismatch. Extra: {actual - expected}, Missing: {expected - actual}"
        )
