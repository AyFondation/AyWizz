# =============================================================================
# File: test_embedding_service.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_embedding_service.py
# Description: Block 2 of the in-app EMBEDDING registry — the provider + model
#              services over in-memory fake stores: CRUD, name/alias uniqueness
#              (409), unknown-provider on model create (422), write-only key
#              encryption (503 without a cipher), and the provider DELETE-GUARD
#              (409 while models still reference it).
# =============================================================================

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingAdapter,
    EmbeddingModelUpsert,
    EmbeddingProviderUpsert,
)
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelService,
    EmbeddingProviderService,
    KeyManagementUnavailableError,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Store:
    """In-memory fake for both provider + model stores (by `_key`)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.docs[document["_key"]] = dict(document)

    async def get(self, key: str) -> dict[str, Any] | None:
        return self.docs.get(key)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return next((d for d in self.docs.values() if d.get("name") == name), None)

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        return next((d for d in self.docs.values() if d.get("alias") == alias), None)

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self.docs.values())

    async def list_for_provider(self, provider_id: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("provider_id") == provider_id]

    async def delete(self, key: str) -> bool:
        return self.docs.pop(key, None) is not None


class _FakeCipher:
    def encrypt(self, plaintext: str, *, aad: str) -> str:
        return f"ct::{aad}::{plaintext}"

    def decrypt(self, token: str, *, aad: str) -> str:
        return token.split("::", 2)[2]


def _ids() -> Any:
    return (f"id{i}" for i in itertools.count()).__next__


def _prov_svc(cipher: Any = None) -> tuple[EmbeddingProviderService, _Store, _Store]:
    prov, model = _Store(), _Store()
    svc = EmbeddingProviderService(
        prov, model, cipher, clock=lambda: "t", id_factory=_ids()
    )
    return svc, prov, model


def _model_svc(prov: _Store, model: _Store) -> EmbeddingModelService:
    return EmbeddingModelService(model, prov, clock=lambda: "t", id_factory=_ids())


# --- Provider ---------------------------------------------------------------


async def test_create_provider_and_duplicate_name_409() -> None:
    svc, _, _ = _prov_svc()
    body = EmbeddingProviderUpsert(
        name="Ollama", adapter=EmbeddingAdapter.OLLAMA, base_url="http://ollama:11434"
    )
    pub = await svc.create_provider(body)
    assert pub.provider_id and pub.key_status == "not_set"
    with pytest.raises(HTTPException) as exc:
        await svc.create_provider(body)
    assert exc.value.status_code == 409


async def test_set_api_key_encrypts_and_decrypts() -> None:
    svc, _, _ = _prov_svc(_FakeCipher())
    pub = await svc.create_provider(
        EmbeddingProviderUpsert(name="OpenAI", adapter=EmbeddingAdapter.OPENAI, base_url="https://api.openai.com")
    )
    keyed = await svc.set_api_key(pub.provider_id, "sk-secret-123")
    assert keyed.key_status == "set"
    assert await svc.get_decrypted_key(pub.provider_id) == "sk-secret-123"


async def test_set_api_key_without_cipher_raises() -> None:
    svc, _, _ = _prov_svc(None)
    pub = await svc.create_provider(
        EmbeddingProviderUpsert(name="P", adapter=EmbeddingAdapter.OLLAMA, base_url="http://x")
    )
    with pytest.raises(KeyManagementUnavailableError):
        await svc.set_api_key(pub.provider_id, "k")


async def test_delete_provider_guarded_while_models_reference_it() -> None:
    svc, prov, model = _prov_svc()
    pub = await svc.create_provider(
        EmbeddingProviderUpsert(name="Ollama", adapter=EmbeddingAdapter.OLLAMA, base_url="http://ollama:11434")
    )
    msvc = _model_svc(prov, model)
    await msvc.create_model(
        EmbeddingModelUpsert(
            alias="mini", provider_id=pub.provider_id,
            upstream_model="all-minilm", dimension=384,
        )
    )
    with pytest.raises(HTTPException) as exc:
        await svc.delete_provider(pub.provider_id)
    assert exc.value.status_code == 409  # in use by a model
    # The message MUST name the provider + model (human-readable), not the
    # opaque provider id — regression for the "still has 1 model(s)" gibberish.
    detail = str(exc.value.detail)
    assert "Ollama" in detail  # provider NAME
    assert "mini" in detail  # model ALIAS
    assert pub.provider_id not in detail  # NOT the opaque id
    # After deleting the model, the provider can be removed.
    models = await msvc.list_models()
    assert await msvc.delete_model(models[0].model_id) is True
    assert await svc.delete_provider(pub.provider_id) is True


# --- Model ------------------------------------------------------------------


async def test_create_model_unknown_provider_422() -> None:
    _, prov, model = _prov_svc()
    msvc = _model_svc(prov, model)
    with pytest.raises(HTTPException) as exc:
        await msvc.create_model(
            EmbeddingModelUpsert(
                alias="x", provider_id="ghost", upstream_model="m", dimension=8
            )
        )
    assert exc.value.status_code == 422


class _FakeProjectRepo:
    """Minimal project-selection store: reports the projects using a model."""

    def __init__(self, using: list[dict[str, Any]]) -> None:
        self._using = using

    async def list_using_model(self, model_id: str) -> list[dict[str, Any]]:
        return self._using


async def test_delete_model_guarded_message_is_human_readable() -> None:
    prov, model = _Store(), _Store()
    psvc = EmbeddingProviderService(prov, model, None, clock=lambda: "t", id_factory=_ids())
    pub = await psvc.create_provider(
        EmbeddingProviderUpsert(name="Ollama", adapter=EmbeddingAdapter.OLLAMA, base_url="http://x")
    )
    project_repo = _FakeProjectRepo([{"project_id": "smoke-p-8c5a3c"}])
    msvc = EmbeddingModelService(
        model, prov, project_repo, clock=lambda: "t", id_factory=_ids()
    )
    created = await msvc.create_model(
        EmbeddingModelUpsert(
            alias="mini", provider_id=pub.provider_id,
            upstream_model="all-minilm", dimension=384,
        )
    )
    with pytest.raises(HTTPException) as exc:
        await msvc.delete_model(created.model_id)
    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "mini" in detail  # model ALIAS, not the opaque id
    assert "smoke-p-8c5a3c" in detail  # the selecting project id
    assert created.model_id not in detail  # NOT the opaque model id


async def test_create_model_duplicate_alias_409() -> None:
    svc, prov, model = _prov_svc()
    pub = await svc.create_provider(
        EmbeddingProviderUpsert(name="P", adapter=EmbeddingAdapter.OLLAMA, base_url="http://x")
    )
    msvc = _model_svc(prov, model)
    body = EmbeddingModelUpsert(
        alias="mini", provider_id=pub.provider_id, upstream_model="all-minilm", dimension=384
    )
    await msvc.create_model(body)
    with pytest.raises(HTTPException) as exc:
        await msvc.create_model(body)
    assert exc.value.status_code == 409
