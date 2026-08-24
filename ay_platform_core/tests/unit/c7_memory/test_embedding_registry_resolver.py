# =============================================================================
# File: test_embedding_registry_resolver.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_embedding_registry_resolver.py
# Description: Unit tests for RegistryEmbedderResolver (D-011, per-project
#              embedder selection). Validates adapter → C7 embedder mapping,
#              the graceful fallbacks (no selection / unknown provider /
#              unsupported adapter / sub-minimum hash dimension → None), and
#              the model_id cache. Fakes stand in for the catalogue service +
#              provider store so the branch logic is exercised in isolation.
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.embedding.ollama import OllamaEmbedder
from ay_platform_core.c7_memory.embedding.openai import OpenAIEmbedder
from ay_platform_core.c7_memory.embedding.registry_resolver import (
    RegistryEmbedderResolver,
)
from ay_platform_core.c8_llm.registry.embedding_catalog_models import (
    ProjectEmbeddingResponse,
)
from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelEntry,
    EmbeddingModelPublic,
    EmbeddingProviderEntry,
)

pytestmark = pytest.mark.unit


def _model(
    *, model_id: str = "m1", provider_id: str = "p1", dim: int = 128
) -> EmbeddingModelPublic:
    return EmbeddingModelEntry(
        model_id=model_id,
        alias="mini",
        provider_id=provider_id,
        upstream_model="all-minilm",
        dimension=dim,
        enabled=True,
        effective_from="2026-01-01T00:00:00+00:00",
    ).to_public()


def _provider_doc(
    adapter: EmbeddingAdapter, base_url: str = "http://ollama:11434"
) -> dict[str, Any]:
    return EmbeddingProviderEntry(
        provider_id="p1",
        name="prov",
        adapter=adapter,
        base_url=base_url,
        effective_from="2026-01-01T00:00:00+00:00",
    ).to_document()


class _FakeCatalog:
    def __init__(self, model: EmbeddingModelPublic | None) -> None:
        self._model = model

    async def get_project_embedding(
        self, tenant_id: str, project_id: str
    ) -> ProjectEmbeddingResponse:
        return ProjectEmbeddingResponse(
            tenant_id=tenant_id,
            project_id=project_id,
            model_id=None if self._model is None else self._model.model_id,
            is_explicit=self._model is not None,
            model=self._model,
        )


class _FakeProviderRepo:
    def __init__(self, doc: dict[str, Any] | None) -> None:
        self._doc = doc
        self.calls = 0

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        self.calls += 1
        return self._doc


class _FakeProviderService:
    """Stands in for EmbeddingProviderService — only get_decrypted_key is
    exercised by the resolver (openai branch)."""

    def __init__(self, key: str | None) -> None:
        self._key = key

    async def get_decrypted_key(self, provider_id: str) -> str | None:
        return self._key


def _resolver(
    model: EmbeddingModelPublic | None,
    provider_doc: dict[str, Any] | None,
    *,
    key: str | None = None,
) -> RegistryEmbedderResolver:
    catalog = _FakeCatalog(model)
    providers = _FakeProviderRepo(provider_doc)
    provider_service = _FakeProviderService(key)
    # The catalog_repo / project_repo / model_repo args are only used by
    # ensure_collections(), never by resolve(); the fakes suffice here.
    return RegistryEmbedderResolver(
        catalog, catalog, catalog, catalog, providers, provider_service  # type: ignore[arg-type]
    )


class TestRegistryEmbedderResolver:
    async def test_no_selection_returns_none(self) -> None:
        r = _resolver(None, None)
        assert await r.resolve("t1", "proj-A") is None

    async def test_blank_ids_return_none(self) -> None:
        r = _resolver(_model(), _provider_doc(EmbeddingAdapter.OLLAMA))
        assert await r.resolve("", "proj-A") is None
        assert await r.resolve("t1", "") is None

    async def test_ollama_adapter_builds_ollama_embedder(self) -> None:
        r = _resolver(_model(), _provider_doc(EmbeddingAdapter.OLLAMA))
        emb = await r.resolve("t1", "proj-A")
        assert isinstance(emb, OllamaEmbedder)
        assert emb.model_id == "all-minilm"
        await emb.aclose()

    async def test_deterministic_adapter_builds_hash_embedder(self) -> None:
        r = _resolver(
            _model(dim=256), _provider_doc(EmbeddingAdapter.DETERMINISTIC_HASH)
        )
        emb = await r.resolve("t1", "proj-A")
        assert isinstance(emb, DeterministicHashEmbedder)
        assert emb.model_id == "all-minilm"
        assert emb.dimension == 256

    async def test_deterministic_below_min_dimension_falls_back(self) -> None:
        r = _resolver(
            _model(dim=4), _provider_doc(EmbeddingAdapter.DETERMINISTIC_HASH)
        )
        assert await r.resolve("t1", "proj-A") is None

    async def test_openai_adapter_with_key_builds_openai_embedder(self) -> None:
        r = _resolver(
            _model(dim=1536),
            _provider_doc(EmbeddingAdapter.OPENAI, base_url="https://api.openai.com/v1"),
            key="sk-test-key",
        )
        emb = await r.resolve("t1", "proj-A")
        assert isinstance(emb, OpenAIEmbedder)
        assert emb.model_id == "all-minilm"
        assert emb.dimension == 1536
        await emb.aclose()

    async def test_openai_adapter_without_key_falls_back(self) -> None:
        # An openai endpoint needs a key; no usable key → global fallback.
        r = _resolver(
            _model(), _provider_doc(EmbeddingAdapter.OPENAI), key=None
        )
        assert await r.resolve("t1", "proj-A") is None

    async def test_unknown_provider_falls_back(self) -> None:
        r = _resolver(_model(), None)
        assert await r.resolve("t1", "proj-A") is None

    async def test_cache_reuses_built_embedder_per_model_id(self) -> None:
        # Deterministic adapter → no httpx client to clean up.
        providers = _FakeProviderRepo(
            _provider_doc(EmbeddingAdapter.DETERMINISTIC_HASH)
        )
        catalog = _FakeCatalog(_model(dim=128))
        r = RegistryEmbedderResolver(
            catalog, catalog, catalog, catalog, providers, _FakeProviderService(None)  # type: ignore[arg-type]
        )
        first = await r.resolve("t1", "proj-A")
        second = await r.resolve("t1", "proj-B")
        assert first is second
        # The provider store is hit once — the second resolve is a cache hit.
        assert providers.calls == 1
