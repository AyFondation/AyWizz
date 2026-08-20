# =============================================================================
# File: seed.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/seed.py
# Description: One-shot, IDEMPOTENT seeding of the provider + model registries
#              from the canonical `litellm-config.yaml`. v2: each model's
#              provider family becomes an explicit PROVIDER (name + mandatory
#              base_url + wire_format) referenced by the model's stable
#              `model_id`. The bootstrap base_url per family is the provider's
#              well-known endpoint (explicit data, not a runtime default — the
#              operator can edit it). API keys are never seeded (set later via
#              the write-only provider endpoint). Existing aliases / provider
#              names are left untouched (preserves operator edits + keys).
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from ay_platform_core.c8_llm.catalog import Feature
from ay_platform_core.c8_llm.config import LiteLLMConfig, ModelEntry
from ay_platform_core.c8_llm.registry.models import (
    LLMRegistryEntry,
    ModelCapabilities,
    ModelQuality,
)
from ay_platform_core.c8_llm.registry.provider_models import LLMProviderEntry
from ay_platform_core.c8_llm.registry.provider_repository import ProviderStore
from ay_platform_core.c8_llm.registry.repository import RegistryStore

# Well-known endpoint per provider family, used ONLY to bootstrap the seed
# provider's mandatory base_url. Operator-editable afterwards. Unknown families
# fall back to a clearly-placeholder URL so the seed never invents a real one.
_PROVIDER_BASE_URL: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com",
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _quality_for(model_entry: ModelEntry) -> ModelQuality:
    upstream = model_entry.litellm_params.model.lower()
    if "opus" in upstream:
        return ModelQuality.HIGH
    if "sonnet" in upstream:
        return ModelQuality.MEDIUM
    if "haiku" in upstream:
        return ModelQuality.LOW
    return ModelQuality.MEDIUM


def _split_upstream(litellm_model: str) -> tuple[str, str]:
    """`anthropic/claude-…` → (wire_format='anthropic', upstream='claude-…')."""
    if "/" in litellm_model:
        wire, rest = litellm_model.split("/", 1)
        return wire, rest
    return litellm_model, litellm_model


async def seed_missing(
    repo: RegistryStore,
    provider_repo: ProviderStore,
    config: LiteLLMConfig,
    *,
    clock: Callable[[], str] | None = None,
    id_factory: Callable[[], str] = _new_id,
) -> list[str]:
    """Seed providers + models for any alias in `config` not already present.
    Returns the aliases created (idempotent — existing rows + their providers'
    keys are left untouched)."""
    stamp = (clock or (lambda: datetime.now(UTC).isoformat()))()
    # 1) Ensure one provider per wire_format, reusing an existing one by name.
    wire_to_provider_id: dict[str, str] = {}
    for entry in config.model_list:
        # Skip routing pass-throughs: the bare `"*"` catch-all AND the neutral
        # tier entries (`model: "*"`), which carry no provider/model to seed.
        # With the provider-independent config (D-011) EVERY entry is a
        # pass-through, so the seed creates nothing — the registry stays EMPTY
        # until an operator registers a provider via the HMI (Option B).
        if entry.model_name == "*" or entry.litellm_params.model == "*":
            continue
        wire, _ = _split_upstream(entry.litellm_params.model)
        if wire in wire_to_provider_id:
            continue
        name = wire.capitalize()
        existing = await provider_repo.get_by_name(name)
        if existing is not None:
            wire_to_provider_id[wire] = str(existing["_key"])
            continue
        prov = LLMProviderEntry(
            provider_id=id_factory(),
            name=name,
            base_url=_PROVIDER_BASE_URL.get(wire, f"https://REPLACE-ME.{wire}.invalid"),
            wire_format=wire,
            effective_from=stamp,
        )
        await provider_repo.upsert(prov.to_document())
        wire_to_provider_id[wire] = prov.provider_id

    # 2) Ensure each model (by alias), referencing its provider id.
    created: list[str] = []
    for entry in config.model_list:
        # Same pass-through skip as the provider loop (bare `"*"` + neutral
        # tiers) — they have no wire_format/provider to reference.
        if entry.model_name == "*" or entry.litellm_params.model == "*":
            continue
        if await repo.get_by_alias(entry.model_name) is not None:
            continue
        wire, upstream = _split_upstream(entry.litellm_params.model)
        features = set(entry.model_info.features)
        model = LLMRegistryEntry(
            model_id=id_factory(),
            alias=entry.model_name,
            provider_id=wire_to_provider_id[wire],
            upstream_model=upstream,
            capabilities=ModelCapabilities(
                vision=Feature.VISION in features,
                tool_calling=Feature.TOOL_CALLING in features,
                context_window=entry.model_info.context_window,
            ),
            provider_cost_in_per_1m=entry.model_info.cost_per_million_input,
            provider_cost_out_per_1m=entry.model_info.cost_per_million_output,
            default_model_quality=_quality_for(entry),
            enabled=True,
            effective_from=stamp,
        )
        await repo.upsert(model.to_document())
        created.append(entry.model_name)
    return created
