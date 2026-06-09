# =============================================================================
# File: key_provider.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/key_provider.py
# Description: Per-request call-target resolver wired into the C8 gateway client.
#              Given the alias the platform routes to, it resolves model →
#              provider and returns a `CallTarget`: the rewritten litellm model
#              string `<provider.wire_format>/<upstream_model>`, the provider's
#              MANDATORY `api_base`, and the decrypted provider key. The client
#              rewrites the request body with these — so routing depends on the
#              provider's explicit endpoint, never a built-in default, and the
#              alias is free to change (model_id is the stable reference).
#
#              GRACEFUL: an unknown alias (e.g. the test `_mock_llm` model) or a
#              dangling provider returns None → the client leaves the body as-is
#              (the proxy/mock handles it). The key is NEVER logged.
# =============================================================================

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from ay_platform_core.c8_llm.registry.provider_repository import (
    LLMProviderRepository,
    ProviderStore,
)
from ay_platform_core.c8_llm.registry.repository import (
    LLMRegistryRepository,
    RegistryStore,
)
from ay_platform_core.crypto.secret_cipher import SecretCipher, SecretCipherError

_log = logging.getLogger("c8_llm.key_provider")


class CallTarget(NamedTuple):
    """Per-call upstream override resolved from the registry + provider.

    `model` is the rewritten litellm model id (`wire_format/upstream_model`);
    `api_base` is the provider's mandatory endpoint; `api_key` is the decrypted
    provider key, or None when no key is stored / no master key is configured."""

    model: str
    api_base: str
    api_key: str | None


def _provider_aad(provider_id: str) -> str:
    return f"llm_provider:{provider_id}:api_key"


class RegistryKeyProvider:
    """Async callable `(alias) -> CallTarget | None` over the model + provider
    registries. Best-effort by construction."""

    def __init__(
        self,
        registry_repo: RegistryStore,
        provider_repo: ProviderStore,
        cipher: SecretCipher | None,
    ) -> None:
        self._registry = registry_repo
        self._providers = provider_repo
        self._cipher = cipher

    async def __call__(self, alias: str) -> CallTarget | None:
        try:
            model_doc = await self._registry.get_by_alias(alias)
            if model_doc is None:
                return None  # unknown alias (e.g. mock model) → no override
            provider_id = model_doc.get("provider_id")
            prov = await self._providers.get(provider_id) if provider_id else None
            if prov is None:
                _log.warning(
                    "model %r references missing provider %r — no override",
                    alias, provider_id,
                )
                return None
            api_key: str | None = None
            ciphertext = prov.get("api_key_ciphertext")
            if ciphertext is not None and self._cipher is not None:
                api_key = self._cipher.decrypt(
                    ciphertext, aad=_provider_aad(str(prov["_key"]))
                )
            model = f"{prov['wire_format']}/{model_doc['upstream_model']}"
            return CallTarget(model=model, api_base=prov["base_url"], api_key=api_key)
        except SecretCipherError as exc:
            _log.warning("call-target resolution failed for alias %s: %s", alias, exc)
            return None


def build_registry_key_provider(db: Any) -> RegistryKeyProvider:
    """Build a call-target resolver over the shared Arango `db`. ALWAYS returns
    a provider: with a master key it injects the decrypted provider key; without
    one it still rewrites the model + injects api_base (not secrets), the key
    falling back to the proxy env key. Wired in each LLM-calling component's app
    factory (option B: C3/C4/C7 hold the master key)."""
    try:
        cipher: SecretCipher | None = SecretCipher.from_env()
    except SecretCipherError:
        _log.warning(
            "AY_SECRET_MASTER_KEY absent — provider api_key injection disabled "
            "(model rewrite + api_base still applied; proxy env key as fallback)"
        )
        cipher = None
    return RegistryKeyProvider(
        LLMRegistryRepository(db), LLMProviderRepository(db), cipher
    )
