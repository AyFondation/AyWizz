# =============================================================================
# File: test_embedding_seed_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_admin/test_embedding_seed_flow.py
# Description: Integration test for the dev-only Ollama embedding registry seed
#              against a REAL ArangoDB with the REAL provider + model services:
#              the seed persists an Ollama provider + the all-minilm model, and
#              a re-run is idempotent (no duplicates). Proves the whole chain
#              (seed → services → repos → Arango) the c8-admin lifespan uses.
#
# @relation validates:R-400-226
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c8_llm.registry.embedding_repository import (
    EmbeddingModelRepository,
    EmbeddingProviderRepository,
)
from ay_platform_core.c8_llm.registry.embedding_seed import seed_ollama_embedding
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelService,
    EmbeddingProviderService,
)
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_BASE_URL = "http://ollama:11434"


@pytest_asyncio.fixture(scope="function")
async def services(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[tuple[EmbeddingProviderService, EmbeddingModelService]]:
    db_name = f"c8_seed_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    prov_repo = EmbeddingProviderRepository(db)
    await prov_repo.ensure_collections()
    model_repo = EmbeddingModelRepository(db)
    await model_repo.ensure_collections()
    provider_service = EmbeddingProviderService(prov_repo, model_repo)
    model_service = EmbeddingModelService(model_repo, prov_repo)
    try:
        yield provider_service, model_service
    finally:
        cleanup_arango_database(arango_container, db_name)


async def test_seed_persists_provider_and_model(
    services: tuple[EmbeddingProviderService, EmbeddingModelService],
) -> None:
    provider_service, model_service = services

    created = await seed_ollama_embedding(
        provider_service, model_service, base_url=_BASE_URL
    )
    assert created is True

    providers = await provider_service.list_providers()
    assert len(providers) == 1
    prov = providers[0]
    assert prov.adapter == "ollama"
    assert prov.base_url == _BASE_URL

    models = await model_service.list_models()
    assert len(models) == 1
    assert models[0].alias == "all-minilm"
    assert models[0].dimension == 384
    assert models[0].provider_id == prov.provider_id


async def test_seed_is_idempotent(
    services: tuple[EmbeddingProviderService, EmbeddingModelService],
) -> None:
    provider_service, model_service = services

    assert await seed_ollama_embedding(
        provider_service, model_service, base_url=_BASE_URL
    ) is True
    # Re-run against the now-populated registry → no-op, no duplicates.
    assert await seed_ollama_embedding(
        provider_service, model_service, base_url=_BASE_URL
    ) is False

    assert len(await provider_service.list_providers()) == 1
    assert len(await model_service.list_models()) == 1
