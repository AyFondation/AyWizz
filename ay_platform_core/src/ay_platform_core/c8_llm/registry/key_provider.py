# =============================================================================
# File: key_provider.py
# Version: 3
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
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
from ay_platform_core.c8_llm.registry.models import ModelQuality
from ay_platform_core.c8_llm.registry.provider_repository import (
    LLMProviderRepository,
    ProviderStore,
)
from ay_platform_core.c8_llm.registry.repository import (
    LLMRegistryRepository,
    RegistryStore,
)
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
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


# ---------------------------------------------------------------------------
# Provider-independent MODEL resolution (D-011)
# ---------------------------------------------------------------------------

# Agent → preferred `model_quality` order. The platform routes an agent to a
# QUALITY TIER (never a model/provider name); the operator's registry catalogue
# then supplies the concrete project model of that quality. Each tuple is a
# FALLBACK chain, so any enabled model the project has is usable.
_HIGH_FIRST = (ModelQuality.HIGH, ModelQuality.MEDIUM, ModelQuality.LOW)
_MEDIUM_FIRST = (ModelQuality.MEDIUM, ModelQuality.HIGH, ModelQuality.LOW)
_LOW_FIRST = (ModelQuality.LOW, ModelQuality.MEDIUM, ModelQuality.HIGH)
_AGENT_QUALITY_ORDER: dict[str, tuple[ModelQuality, ...]] = {
    "architect": _HIGH_FIRST,
    "c6-judge": _HIGH_FIRST,
    "sub-agent": _LOW_FIRST,
    "c7-kg-extractor": _LOW_FIRST,
    "c7-contextualizer": _LOW_FIRST,
    "summarizer": _LOW_FIRST,
    "decontextualizer": _LOW_FIRST,
    "densifier": _LOW_FIRST,
    "image_analyzer": _LOW_FIRST,
}

# `(agent_name, tenant_id, project_id, *, require_tool_calling) -> alias | None`
ModelResolver = Callable[..., Awaitable[str | None]]


async def _resolve_model_via_catalog(
    catalog: Any,
    agent_name: str,
    tenant_id: str | None,
    project_id: str | None,
    *,
    require_tool_calling: bool,
) -> str | None:
    """Core resolution (extracted for testing without a DB): try the agent's
    quality tiers in order, returning the first project model that resolves."""
    if not tenant_id:
        return None
    for quality in _AGENT_QUALITY_ORDER.get(agent_name, _MEDIUM_FIRST):
        try:
            resolved = await catalog.resolve(
                tenant_id,
                quality,
                project_id=project_id,
                require_tool_calling=require_tool_calling,
            )
        except Exception as exc:  # best-effort, never break a call
            _log.warning("model resolution failed for %s: %s", agent_name, exc)
            return None
        if resolved is not None:
            return str(resolved.model_alias)
    return None


def build_registry_model_resolver(db: Any) -> ModelResolver:
    """Resolve an agent's call to a CONCRETE model alias from the operator's
    registry catalogue, scoped to the project (provider-INDEPENDENT, D-011 — no
    model/provider name in config). Maps the agent to a quality tier, then
    returns the project's cheapest enabled model of that quality, falling back
    across qualities so ANY registered project model works. None → no model is
    configured for the project yet (caller leaves the model unset). Best-effort:
    a registry/DB error yields None and NEVER breaks the LLM call."""
    catalog = TenantCatalogService(
        TenantCatalogRepository(db), LLMRegistryService(LLMRegistryRepository(db))
    )

    async def resolve(
        agent_name: str,
        tenant_id: str | None,
        project_id: str | None,
        *,
        require_tool_calling: bool = False,
    ) -> str | None:
        return await _resolve_model_via_catalog(
            catalog,
            agent_name,
            tenant_id,
            project_id,
            require_tool_calling=require_tool_calling,
        )

    return resolve
