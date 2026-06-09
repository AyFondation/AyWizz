# =============================================================================
# File: secret_cipher.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/crypto/secret_cipher.py
# Description: Application secret cipher — AES-256-GCM over an ENV-provided
#              master keyring. Defensive at-rest encryption for REVERSIBLE
#              secrets (LLM provider API keys). Passwords are NOT handled here
#              (one-way Argon2id lives in C2 — never decrypt a password).
#
#              The master key(s) live ONLY in the environment (a K8s Secret,
#              optionally Vault-backed); ONLY the ciphertext is persisted in the
#              DB. An external KMS and per-tenant envelope DEKs are deliberately
#              OUT of v1 scope — this `local-master-key` cipher is the seam they
#              would extend (swap the keyring source, keep the token format).
#
#              Token format (self-describing, rotation-friendly):
#                  ay.1.<key_id>.<nonce_b64u>.<ciphertext+tag_b64u>
#              The AAD binds a ciphertext to its CONTEXT (e.g.
#              "llm_registry:<alias>:api_key") so it cannot be replayed into
#              another field/row.
# =============================================================================

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = "1"
_PREFIX = "ay"
_NONCE_BYTES = 12  # 96-bit GCM nonce (random per encryption — never reused)
_KEY_BYTES = 32  # AES-256


class SecretCipherError(Exception):
    """Raised on misconfiguration or a failed / forged decryption."""


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass(frozen=True)
class SecretCipher:
    """AES-256-GCM cipher over a keyring.

    `keys` maps key_id -> 32-byte key. `active_key_id` selects the ENCRYPT key;
    EVERY key in `keys` can decrypt. Rotation: add a new active key, keep the
    old ones to read existing ciphertexts, re-encrypt lazily, then retire the
    old key once nothing references it (decryption of its tokens then fails
    loudly — by design)."""

    keys: dict[str, bytes]
    active_key_id: str

    def __post_init__(self) -> None:
        if self.active_key_id not in self.keys:
            raise SecretCipherError("active key id not present in keyring")
        for kid, key in self.keys.items():
            if len(key) != _KEY_BYTES:
                raise SecretCipherError(
                    f"master key {kid!r} must be {_KEY_BYTES} bytes (AES-256)"
                )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SecretCipher:
        """Build from the environment.

        `AY_SECRET_MASTER_KEYS` = "id:b64,id:b64,…" (the FIRST entry is active,
        the rest are decrypt-only retired keys), OR the single
        `AY_SECRET_MASTER_KEY` (b64 of 32 bytes) with optional
        `AY_SECRET_MASTER_KEY_ID` (default "k1"). Keys are url-safe base64 of 32
        raw bytes (generate with `os.urandom(32)`)."""
        e = env if env is not None else dict(os.environ)
        entries: list[tuple[str, bytes]] = []
        multi = e.get("AY_SECRET_MASTER_KEYS", "").strip()
        if multi:
            for part in multi.split(","):
                kid, sep, b64 = part.partition(":")
                if not sep or not kid.strip() or not b64.strip():
                    raise SecretCipherError("malformed AY_SECRET_MASTER_KEYS entry")
                entries.append((kid.strip(), _b64u_decode(b64.strip())))
        else:
            single = e.get("AY_SECRET_MASTER_KEY", "").strip()
            if not single:
                raise SecretCipherError(
                    "no master key configured (set AY_SECRET_MASTER_KEY[S])"
                )
            kid = e.get("AY_SECRET_MASTER_KEY_ID", "k1").strip() or "k1"
            entries.append((kid, _b64u_decode(single)))
        return cls(keys=dict(entries), active_key_id=entries[0][0])

    def encrypt(self, plaintext: str, *, aad: str) -> str:
        """Encrypt `plaintext` bound to context `aad`. Returns a token string."""
        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(self.keys[self.active_key_id]).encrypt(
            nonce, plaintext.encode("utf-8"), aad.encode("utf-8")
        )
        return ".".join(
            (_PREFIX, _VERSION, self.active_key_id, _b64u_encode(nonce), _b64u_encode(ct))
        )

    def decrypt(self, token: str, *, aad: str) -> str:
        """Decrypt a token produced by `encrypt`. The SAME `aad` is required —
        a mismatch (or any tamper) raises `SecretCipherError`."""
        parts = token.split(".")
        if len(parts) != 5:
            raise SecretCipherError("malformed secret token")
        prefix, version, key_id, nonce_b64, ct_b64 = parts
        if prefix != _PREFIX or version != _VERSION:
            raise SecretCipherError("unsupported secret token format")
        key = self.keys.get(key_id)
        if key is None:
            raise SecretCipherError(f"unknown master key id {key_id!r} (rotated out?)")
        try:
            pt = AESGCM(key).decrypt(
                _b64u_decode(nonce_b64), _b64u_decode(ct_b64), aad.encode("utf-8")
            )
        except InvalidTag as exc:
            raise SecretCipherError("decryption failed (tampered or wrong context)") from exc
        return pt.decode("utf-8")


def masked_suffix(secret: str, *, keep: int = 4) -> str:
    """A non-reversible display hint for a WRITE-ONLY secret (e.g. `…a1b2`).
    Used by HMI/API responses so the operator can recognise a stored key without
    the API ever returning the plaintext."""
    if not secret:
        return ""
    tail = secret[-keep:] if len(secret) > keep else secret
    return "…" + tail
