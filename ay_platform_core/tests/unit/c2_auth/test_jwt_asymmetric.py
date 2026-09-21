# =============================================================================
# File: test_jwt_asymmetric.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_jwt_asymmetric.py
# Description: Unit tests for the RS256 / asymmetric JWT path of AuthService.
#
#              WHY THIS FILE EXISTS. `AuthConfig.jwt_algorithm` documents
#              "HS256 for dev, RS256/EdDSA for prod" (R-100-038) — yet the
#              2026-09-20 branch audit found that `_signing_key()` and
#              `_verification_key()` had NEVER been exercised on their
#              non-HS256 side. Both are two-line selectors:
#
#                  if self._config.jwt_algorithm == "HS256":
#                      return self._config.jwt_secret_key
#                  return self._config.jwt_private_key   # or public_key
#
#              so the whole PRODUCTION signing path rested on a branch no
#              test had ever taken. A swap of the two returns, or a key field
#              read from the wrong side, would leave every dev suite green and
#              fail only once deployed — or, worse, sign with a key the
#              verifier does not expect.
#
#              These tests use a REAL generated RSA keypair rather than a
#              stub: the point is that PyJWT accepts what the service hands
#              it, which a fake string could never prove.
#
# @relation validates:R-100-038
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.models import (
    LoginRequest,
    RBACGlobalRole,
    UserInternal,
    UserStatus,
)
from ay_platform_core.c2_auth.modes.local_mode import LocalMode
from ay_platform_core.c2_auth.service import AuthService

pytestmark = pytest.mark.unit

_REQUEST = LoginRequest(username="user", password="correct")
_HS256_SECRET = "test-secret-key-32-chars-minimum!"


def _rsa_keypair() -> tuple[str, str]:
    """A real 2048-bit RSA keypair, PEM-encoded as the config expects."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    # Module-scoped: RSA generation is the slow part of this file, and every
    # test wants the same, valid pair.
    return _rsa_keypair()


def _repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_user_by_username.return_value = UserInternal(
        user_id="u-1",
        username="user",
        tenant_id="t-1",
        roles=[RBACGlobalRole.USER],
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        argon2id_hash=LocalMode.hash_password("correct"),
    )
    repo.reset_failed_attempts.return_value = None
    repo.insert_session.return_value = None
    repo.get_project_scopes.return_value = {}
    repo.get_tenant.return_value = None
    # `verify_token` does a stateful revocation check (R-100-073) and reads
    # `session["active"]`. Stated explicitly rather than left to AsyncMock's
    # auto-generated return: a MagicMock is truthy, so the check would pass
    # for the wrong reason and the test would prove nothing about sessions.
    repo.get_session.return_value = {"active": True}
    return repo


def _rs256_service(keypair: tuple[str, str]) -> AuthService:
    private_pem, public_pem = keypair
    config = AuthConfig.model_validate({
        "auth_mode": "local",
        "jwt_algorithm": "RS256",
        # Deliberately ALSO set a symmetric secret. If the selector ever falls
        # through to `jwt_secret_key` on the RS256 path, this value would let
        # signing succeed with the wrong key instead of failing loudly — so
        # the assertions below have to check the algorithm and the key, not
        # merely that a token came back.
        "jwt_secret_key": _HS256_SECRET,
        "jwt_private_key": private_pem,
        "jwt_public_key": public_pem,
        "platform_environment": "testing",
    })
    return AuthService(config, _repo())


async def test_issued_token_is_actually_signed_with_rs256(
    keypair: tuple[str, str],
) -> None:
    """The JOSE header SHALL announce RS256, not the HS256 default."""
    service = _rs256_service(keypair)
    resp = await service.issue_token(_REQUEST)

    header = jwt.get_unverified_header(resp.access_token)
    assert header["alg"] == "RS256"


async def test_token_verifies_against_the_public_key_and_not_the_secret(
    keypair: tuple[str, str],
) -> None:
    """The signature SHALL be the private key's, verifiable by the public one.

    This is the assertion that catches a swapped selector: a token signed
    with `jwt_secret_key` would still decode somewhere, just not here.
    """
    _, public_pem = keypair
    service = _rs256_service(keypair)
    resp = await service.issue_token(_REQUEST)

    decoded = jwt.decode(
        resp.access_token, public_pem, algorithms=["RS256"], audience="platform"
    )
    assert decoded["sub"] == "u-1"

    # And the symmetric secret must NOT verify it.
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(
            resp.access_token,
            _HS256_SECRET,
            algorithms=["HS256"],
            audience="platform",
        )


async def test_service_verifies_its_own_rs256_token(
    keypair: tuple[str, str],
) -> None:
    """`_verification_key()` SHALL select the PUBLIC key, closing the loop.

    Signing and verification read two different config fields, so a token
    that round-trips through the service proves both selectors agree — the
    failure this covers is an RS256 deployment where every freshly issued
    token is rejected by the very service that issued it.
    """
    service = _rs256_service(keypair)
    resp = await service.issue_token(_REQUEST)

    claims = await service.verify_token(resp.access_token)
    assert claims.sub == "u-1"
    assert claims.tenant_id == "t-1"


async def test_rs256_rejects_a_token_signed_by_a_foreign_key(
    keypair: tuple[str, str],
) -> None:
    """A token from ANOTHER private key SHALL be refused.

    Without this, a verifier that silently accepted any RS256 token — or fell
    back to an unverified decode — would pass every test above.
    """
    foreign_private, _ = _rsa_keypair()
    service = _rs256_service(keypair)
    resp = await service.issue_token(_REQUEST)
    payload = jwt.decode(
        resp.access_token,
        keypair[1],
        algorithms=["RS256"],
        audience="platform",
    )
    forged = jwt.encode(payload, foreign_private, algorithm="RS256")

    with pytest.raises(HTTPException) as exc_info:
        await service.verify_token(forged)
    assert exc_info.value.status_code == 401


async def test_hs256_still_uses_the_symmetric_secret(
    keypair: tuple[str, str],
) -> None:
    """Regression guard on the OTHER side of the same selector.

    The two branches share one `if`; a fix to the RS256 side that inverted
    the condition would break every existing deployment. Asserted here so
    that failure lands in this file, next to its cause.
    """
    private_pem, public_pem = keypair
    config = AuthConfig.model_validate({
        "auth_mode": "local",
        "jwt_algorithm": "HS256",
        "jwt_secret_key": _HS256_SECRET,
        # Present but must be IGNORED on the HS256 path.
        "jwt_private_key": private_pem,
        "jwt_public_key": public_pem,
        "platform_environment": "testing",
    })
    service = AuthService(config, _repo())
    resp = await service.issue_token(_REQUEST)

    assert jwt.get_unverified_header(resp.access_token)["alg"] == "HS256"
    decoded = jwt.decode(
        resp.access_token,
        _HS256_SECRET,
        algorithms=["HS256"],
        audience="platform",
    )
    assert decoded["sub"] == "u-1"
