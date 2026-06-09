# =============================================================================
# File: __init__.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/crypto/__init__.py
# Description: Shared application cryptography — at-rest encryption of reversible
#              secrets (e.g. LLM provider API keys). Passwords are NOT here
#              (Argon2id in C2).
# =============================================================================

from ay_platform_core.crypto.secret_cipher import (
    SecretCipher,
    SecretCipherError,
    masked_suffix,
)

__all__ = ["SecretCipher", "SecretCipherError", "masked_suffix"]
