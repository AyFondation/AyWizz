# =============================================================================
# File: test_embedding_seed.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_embedding_seed.py
# Description: Unit tests for the dev-only Ollama embedding registry seed
#              (embedding_seed.py): it registers a provider + all-minilm model
#              on an empty registry, is idempotent on re-run and when the
#              provider/model already exist, and passes the configured base_url
#              through. Fake services isolate the seed's orchestration from the
#              real registry internals (tested at the integration tier).
#
# @relation validates:R-400-226
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelUpsert,
    EmbeddingProviderUpsert,
)
from ay_platform_core.c8_llm.registry.embedding_seed import seed_ollama_embedding

pytestmark = pytest.mark.unit


@dataclass
class _Provider:
    provider_id: str
    name: str
    adapter: str
    base_url: str


@dataclass
class _Model:
    model_id: str
    alias: str
    provider_id: str


class _FakeProviderService:
    def __init__(self) -> None:
        self.providers: list[_Provider] = []
        self._n = 0

    async def list_providers(self) -> list[_Provider]:
        return list(self.providers)

    async def create_provider(self, body: EmbeddingProviderUpsert) -> _Provider:
        self._n += 1
        p = _Provider(f"prov-{self._n}", body.name, body.adapter, body.base_url)
        self.providers.append(p)
        return p


class _FakeModelService:
    def __init__(self) -> None:
        self.models: list[_Model] = []
        self.created: list[EmbeddingModelUpsert] = []
        self._n = 0

    async def list_models(self) -> list[_Model]:
        return list(self.models)

    async def create_model(self, body: EmbeddingModelUpsert) -> _Model:
        self.created.append(body)
        self._n += 1
        m = _Model(f"mod-{self._n}", body.alias, body.provider_id)
        self.models.append(m)
        return m


def _svcs() -> tuple[Any, Any]:
    return _FakeProviderService(), _FakeModelService()


async def test_seeds_provider_and_model_on_empty_registry() -> None:
    ps, ms = _svcs()
    created = await seed_ollama_embedding(
        ps, ms, base_url="http://ollama:11434"
    )
    assert created is True
    assert len(ps.providers) == 1
    p = ps.providers[0]
    assert p.adapter == EmbeddingAdapter.OLLAMA
    assert p.base_url == "http://ollama:11434"
    assert len(ms.models) == 1
    m = ms.created[0]
    assert m.alias == "all-minilm"
    assert m.upstream_model == "all-minilm"
    assert m.dimension == 384
    assert m.provider_id == p.provider_id  # model bound to the seeded provider


async def test_idempotent_on_rerun() -> None:
    ps, ms = _svcs()
    await seed_ollama_embedding(ps, ms, base_url="http://ollama:11434")
    # Second run: everything already exists → no-op, nothing duplicated.
    created = await seed_ollama_embedding(ps, ms, base_url="http://ollama:11434")
    assert created is False
    assert len(ps.providers) == 1
    assert len(ms.models) == 1


async def test_reuses_existing_provider_by_name() -> None:
    ps, ms = _svcs()
    # Operator already declared the provider (same name) but no model yet.
    ps.providers.append(
        _Provider("op-1", "Ollama (local)", EmbeddingAdapter.OLLAMA, "http://x")
    )
    created = await seed_ollama_embedding(ps, ms, base_url="http://ollama:11434")
    assert created is True  # it created the missing model
    assert len(ps.providers) == 1  # did NOT create a second provider
    assert ms.created[0].provider_id == "op-1"  # model bound to the existing one


async def test_skips_model_when_alias_present() -> None:
    ps, ms = _svcs()
    ps.providers.append(
        _Provider("op-1", "Ollama (local)", EmbeddingAdapter.OLLAMA, "http://x")
    )
    ms.models.append(_Model("m0", "all-minilm", "op-1"))
    created = await seed_ollama_embedding(ps, ms, base_url="http://ollama:11434")
    assert created is False  # provider + model both already present
    assert ms.created == []  # no model create attempted
