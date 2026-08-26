# =============================================================================
# File: registry_resolver.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c7_memory/embedding/registry_resolver.py
# Description: Per-project embedder resolution from the in-app EMBEDDING
#              registry (D-011). Maps a `(tenant, project)` to the concrete
#              adapter the tenant selected for that project (catalogue →
#              model → provider), builds the matching C7 embedder, and caches
#              it by `model_id`. Returns `None` when the project has no
#              registry-backed selection. The `openai` adapter is key-bearing:
#              its provider api_key is decrypted per resolution via the
#              SecretCipher (wired here); `ollama` / `deterministic-hash` are
#              keyless. An `openai` provider with no usable key (or no master
#              key configured) falls back to the caller's global env embedder
#              rather than failing.
#
# @relation implements:R-400-227
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ay_platform_core.c7_memory.embedding.base import EmbeddingProvider
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.embedding.ollama import OllamaEmbedder
from ay_platform_core.c7_memory.embedding.openai import OpenAIEmbedder
from ay_platform_core.c8_llm.registry.embedding_catalog_repository import (
    EmbeddingCatalogRepository,
    EmbeddingProjectRepository,
)
from ay_platform_core.c8_llm.registry.embedding_catalog_service import (
    EmbeddingCatalogService,
)
from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelPublic,
    EmbeddingProviderEntry,
)
from ay_platform_core.c8_llm.registry.embedding_repository import (
    EmbeddingModelRepository,
    EmbeddingProviderRepository,
)
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingProviderService,
    KeyManagementUnavailableError,
)
from ay_platform_core.crypto.secret_cipher import SecretCipher, SecretCipherError

_log = logging.getLogger(__name__)

# DeterministicHashEmbedder requires dimension >= 8; a smaller registry model
# with this adapter would raise at construction. Guard rather than crash a
# retrieval / ingestion request.
_MIN_HASH_DIMENSION = 8


class RegistryEmbedderResolver:
    """Resolves `(tenant, project)` → a C7 embedder from the embedding
    registry, caching built adapters by `model_id`."""

    def __init__(
        self,
        catalog_service: EmbeddingCatalogService,
        catalog_repo: EmbeddingCatalogRepository,
        project_repo: EmbeddingProjectRepository,
        model_repo: EmbeddingModelRepository,
        provider_repo: EmbeddingProviderRepository,
        provider_service: EmbeddingProviderService,
    ) -> None:
        self._catalog = catalog_service
        self._catalog_repo = catalog_repo
        self._project_repo = project_repo
        self._model_repo = model_repo
        self._providers = provider_repo
        # Owns the SecretCipher — decrypts the openai provider key per build.
        self._provider_service = provider_service
        self._cache: dict[str, EmbeddingProvider] = {}
        self._lock = asyncio.Lock()

    async def ensure_collections(self) -> None:
        """Idempotently create the registry collections this resolver reads.

        C7 reads collections the C8 admin surface owns; ensuring them at C7
        startup removes any cross-service startup-order fragility. Empty
        collections simply yield no project selection → global fallback."""
        await self._catalog_repo.ensure_collections()
        await self._project_repo.ensure_collections()
        await self._model_repo.ensure_collections()
        await self._providers.ensure_collections()

    async def resolve(
        self, tenant_id: str, project_id: str
    ) -> EmbeddingProvider | None:
        if not tenant_id or not project_id:
            return None
        resp = await self._catalog.get_project_embedding(tenant_id, project_id)
        model = resp.model
        if model is None:
            return None
        cached = self._cache.get(model.model_id)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cache.get(model.model_id)
            if cached is not None:
                return cached
            built = await self._build(model)
            if built is not None:
                self._cache[model.model_id] = built
            return built

    async def _build(self, model: EmbeddingModelPublic) -> EmbeddingProvider | None:
        doc = await self._providers.get(model.provider_id)
        if doc is None:
            _log.warning(
                "embedding model %s references unknown provider %s — "
                "project falls back to the global embedder",
                model.model_id,
                model.provider_id,
            )
            return None
        provider = EmbeddingProviderEntry.from_document(doc)
        adapter = provider.adapter
        if adapter == EmbeddingAdapter.DETERMINISTIC_HASH:
            if model.dimension < _MIN_HASH_DIMENSION:
                _log.warning(
                    "deterministic-hash model %s dimension %d < %d — "
                    "project falls back to the global embedder",
                    model.model_id,
                    model.dimension,
                    _MIN_HASH_DIMENSION,
                )
                return None
            return DeterministicHashEmbedder(
                model_id=model.upstream_model, dimension=model.dimension
            )
        if adapter == EmbeddingAdapter.OLLAMA:
            return OllamaEmbedder(
                base_url=provider.base_url, model_id=model.upstream_model
            )
        if adapter == EmbeddingAdapter.OPENAI:
            return await self._build_openai(model, provider)
        # Any other adapter has no C7 implementation. Fall back to the global
        # embedder rather than fail the request.
        _log.warning(
            "embedding adapter %s is not implemented in C7 — project falls "
            "back to the global embedder (model %s)",
            adapter,
            model.model_id,
        )
        return None

    async def _build_openai(
        self, model: EmbeddingModelPublic, provider: EmbeddingProviderEntry
    ) -> EmbeddingProvider | None:
        """Build an OpenAI-compatible embedder with the provider's DECRYPTED
        key. Falls back (returns None) when no usable key resolves — an openai
        endpoint needs one, and a silent fallback beats a failed request."""
        try:
            key = await self._provider_service.get_decrypted_key(model.provider_id)
        except KeyManagementUnavailableError:
            key = None
        if not key:
            _log.warning(
                "openai embedding provider %s has no usable api key (or no "
                "master key configured) — project falls back to the global "
                "embedder (model %s)",
                model.provider_id,
                model.model_id,
            )
            return None
        return OpenAIEmbedder(
            base_url=provider.base_url,
            model_id=model.upstream_model,
            api_key=key,
            dimension=model.dimension,
        )


def build_embedding_resolver(db: Any) -> RegistryEmbedderResolver:
    """Wire a per-project embedder resolver over the shared Arango `db`.

    Mirrors `build_registry_key_provider` (chat side): C7's app factory builds
    it once and passes it to `MemoryService`. The registry collections are the
    same ones the C8 admin surface writes to; here they are read-only.
    """
    catalog_repo = EmbeddingCatalogRepository(db)
    project_repo = EmbeddingProjectRepository(db)
    model_repo = EmbeddingModelRepository(db)
    provider_repo = EmbeddingProviderRepository(db)
    catalog_service = EmbeddingCatalogService(catalog_repo, project_repo, model_repo)
    # SecretCipher decrypts openai provider keys. Absent master key → key-bearing
    # (openai) providers fall back; keyless adapters (ollama, hash) still resolve.
    try:
        cipher: SecretCipher | None = SecretCipher.from_env()
    except SecretCipherError:
        _log.warning(
            "AY_SECRET_MASTER_KEY absent — openai embedding providers will fall "
            "back (ollama / deterministic-hash unaffected)"
        )
        cipher = None
    provider_service = EmbeddingProviderService(provider_repo, model_repo, cipher)
    return RegistryEmbedderResolver(
        catalog_service,
        catalog_repo,
        project_repo,
        model_repo,
        provider_repo,
        provider_service,
    )
