# =============================================================================
# File: test_secret_cipher.py
# Version: 1
# Path: ay_platform_core/tests/unit/crypto/test_secret_cipher.py
# Description: Unit tests for the application secret cipher (AES-256-GCM,
#              env-provided master keyring). Covers round-trip, AAD binding,
#              tamper detection, key rotation, env parsing, and masking.
# =============================================================================

from __future__ import annotations

import base64
import os

import pytest

from ay_platform_core.crypto import SecretCipher, SecretCipherError, masked_suffix


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _cipher(*key_ids: str) -> SecretCipher:
    ids = key_ids or ("k1",)
    keys = {kid: os.urandom(32) for kid in ids}
    return SecretCipher(keys=keys, active_key_id=ids[0])


@pytest.mark.unit
def test_round_trip() -> None:
    c = _cipher()
    token = c.encrypt("sk-secret-value", aad="llm_registry:claude:api_key")
    assert token.startswith("ay.1.k1.")
    assert "sk-secret-value" not in token  # not stored in cleartext
    assert c.decrypt(token, aad="llm_registry:claude:api_key") == "sk-secret-value"


@pytest.mark.unit
def test_nonce_is_random_per_encryption() -> None:
    c = _cipher()
    t1 = c.encrypt("x", aad="a")
    t2 = c.encrypt("x", aad="a")
    assert t1 != t2  # distinct nonces → distinct ciphertexts for the same input


@pytest.mark.unit
def test_aad_mismatch_is_rejected() -> None:
    c = _cipher()
    token = c.encrypt("v", aad="registry:claude:api_key")
    with pytest.raises(SecretCipherError, match="tampered or wrong context"):
        c.decrypt(token, aad="registry:OTHER:api_key")  # replayed into another field


@pytest.mark.unit
def test_tampered_ciphertext_is_rejected() -> None:
    c = _cipher()
    token = c.encrypt("value", aad="a")
    head, _, ct = token.rpartition(".")
    flipped = ct[:-2] + ("AA" if ct[-2:] != "AA" else "BB")
    with pytest.raises(SecretCipherError):
        c.decrypt(f"{head}.{flipped}", aad="a")


@pytest.mark.unit
def test_unknown_key_id_is_rejected() -> None:
    enc = _cipher("k2")
    token = enc.encrypt("v", aad="a")
    only_k1 = _cipher("k1")  # does not hold k2
    with pytest.raises(SecretCipherError, match="unknown master key"):
        only_k1.decrypt(token, aad="a")


@pytest.mark.unit
def test_rotation_new_active_old_still_decrypts() -> None:
    k1, k2 = os.urandom(32), os.urandom(32)
    old = SecretCipher(keys={"k1": k1}, active_key_id="k1")
    legacy_token = old.encrypt("legacy", aad="a")
    # Rotate: k2 active, k1 retained for decrypt.
    rotated = SecretCipher(keys={"k1": k1, "k2": k2}, active_key_id="k2")
    assert rotated.decrypt(legacy_token, aad="a") == "legacy"  # old token still readable
    new_token = rotated.encrypt("fresh", aad="a")
    assert new_token.startswith("ay.1.k2.")  # new writes use the active key


@pytest.mark.unit
def test_malformed_token_is_rejected() -> None:
    c = _cipher()
    with pytest.raises(SecretCipherError, match="malformed"):
        c.decrypt("not-a-token", aad="a")


@pytest.mark.unit
def test_rejects_non_256bit_key() -> None:
    with pytest.raises(SecretCipherError, match="AES-256"):
        SecretCipher(keys={"k1": b"too-short"}, active_key_id="k1")


@pytest.mark.unit
def test_active_key_must_be_in_keyring() -> None:
    with pytest.raises(SecretCipherError, match="active key id"):
        SecretCipher(keys={"k1": os.urandom(32)}, active_key_id="k2")


@pytest.mark.unit
def test_from_env_single_key() -> None:
    key = os.urandom(32)
    c = SecretCipher.from_env({"AY_SECRET_MASTER_KEY": _b64(key), "AY_SECRET_MASTER_KEY_ID": "kx"})
    token = c.encrypt("v", aad="a")
    assert token.startswith("ay.1.kx.")
    assert c.decrypt(token, aad="a") == "v"


@pytest.mark.unit
def test_from_env_multi_key_first_is_active() -> None:
    k_new, k_old = os.urandom(32), os.urandom(32)
    c = SecretCipher.from_env(
        {"AY_SECRET_MASTER_KEYS": f"k2:{_b64(k_new)}, k1:{_b64(k_old)}"}
    )
    assert c.active_key_id == "k2"
    # An old token (encrypted under k1) is still decryptable.
    old = SecretCipher(keys={"k1": k_old}, active_key_id="k1")
    assert c.decrypt(old.encrypt("legacy", aad="a"), aad="a") == "legacy"


@pytest.mark.unit
def test_from_env_missing_key_raises() -> None:
    with pytest.raises(SecretCipherError, match="no master key configured"):
        SecretCipher.from_env({})


@pytest.mark.unit
def test_masked_suffix() -> None:
    assert masked_suffix("sk-ant-api03-abcd1234") == "…1234"
    assert masked_suffix("ab") == "…ab"
    assert masked_suffix("") == ""
