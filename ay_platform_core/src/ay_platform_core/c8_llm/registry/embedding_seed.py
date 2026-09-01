# =============================================================================
# File: embedding_seed.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_seed.py
# Description: OPTIONAL, IDEMPOTENT dev-convenience seed of the embedding
#              registry with a local Ollama provider + the `all-minilm` model,
#              so a fresh dev/local cluster has a usable embedder out of the box
#              (the `ollama-seed` Job already pulls the model into Ollama).
#
#              This DELIBERATELY runs ONLY when opted in via
#              `C8_SEED_OLLAMA_EMBEDDING` (set true in the dev overlay, false
#              everywhere else). D-011 keeps the embedding registry EMPTY by
#              default in base/prod — operators declare their real providers
#              via the HMI. This seed is a local-dev shortcut, consistent with
#              the "Ollama = local-dev-only" policy.
#
# @relation implements:R-400-226
# =============================================================================

from __future__ import annotations

import logging

from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelUpsert,
    EmbeddingProviderUpsert,
)
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelService,
    EmbeddingProviderService,
)

_log = logging.getLogger(__name__)

# Defaults for the local dev Ollama embedder. `all-minilm` is what the
# `ollama-seed` Job pulls; its output dimension is 384.
_PROVIDER_NAME = "Ollama (local)"
_MODEL_ALIAS = "all-minilm"
_UPSTREAM_MODEL = "all-minilm"
_DIMENSION = 384


async def seed_ollama_embedding(
    provider_service: EmbeddingProviderService,
    model_service: EmbeddingModelService,
    *,
    base_url: str,
    provider_name: str = _PROVIDER_NAME,
    model_alias: str = _MODEL_ALIAS,
    upstream_model: str = _UPSTREAM_MODEL,
    dimension: int = _DIMENSION,
) -> bool:
    """Register a local Ollama embedding provider + the `all-minilm` model if
    absent. Idempotent: the provider is matched by NAME and the model by ALIAS,
    so a re-run (or a run after an operator already declared them) is a no-op.
    Returns True when it created anything, False when everything already existed.
    """
    providers = await provider_service.list_providers()
    match = next((p for p in providers if p.name == provider_name), None)
    created = False
    if match is None:
        match = await provider_service.create_provider(
            EmbeddingProviderUpsert(
                name=provider_name,
                adapter=EmbeddingAdapter.OLLAMA,
                base_url=base_url,
            )
        )
        created = True
        _log.info("seeded Ollama embedding provider %r (%s)", provider_name, base_url)
    provider_id = match.provider_id

    models = await model_service.list_models()
    if any(m.alias == model_alias for m in models):
        return created
    await model_service.create_model(
        EmbeddingModelUpsert(
            alias=model_alias,
            provider_id=provider_id,
            upstream_model=upstream_model,
            dimension=dimension,
            enabled=True,
        )
    )
    _log.info(
        "seeded Ollama embedding model %r (upstream=%s, dim=%d)",
        model_alias, upstream_model, dimension,
    )
    return True
