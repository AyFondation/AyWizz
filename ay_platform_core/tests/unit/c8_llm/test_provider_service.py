# =============================================================================
# File: test_provider_service.py
# Path: ay_platform_core/tests/unit/c8_llm/test_provider_service.py
# Description: Unit tests for LLMProviderService: create mints an id, name
#              uniqueness (409), update by id, write-only key (encrypt + decrypt,
#              503 without a master key, 404 unknown), delete.
# =============================================================================

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c8_llm.registry.provider_models import LLMProviderUpsert
from ay_platform_core.c8_llm.registry.provider_service import (
    KeyManagementUnavailableError,
    LLMProviderService,
    ProviderNotFoundError,
)
from ay_platform_core.crypto.secret_cipher import SecretCipher

pytestmark = pytest.mark.asyncio


class _FakeRepo:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.store[document["_key"]] = dict(document)

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return self.store.get(provider_id)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        for doc in self.store.values():
            if doc.get("name") == name:
                return doc
        return None

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self.store.values())

    async def delete(self, provider_id: str) -> bool:
        return self.store.pop(provider_id, None) is not None


def _service(cipher: SecretCipher | None = None) -> tuple[LLMProviderService, _FakeRepo]:
    repo = _FakeRepo()
    ids = (f"p{i}" for i in itertools.count())
    svc = LLMProviderService(
        repo,
        cipher,
        clock=lambda: "2026-06-08T00:00:00+00:00",
        id_factory=lambda: next(ids),
    )
    return svc, repo


def _body(
    name: str = "Anthropic", base_url: str = "https://api.anthropic.com"
) -> LLMProviderUpsert:
    return LLMProviderUpsert(name=name, base_url=base_url, wire_format="anthropic")


def _cipher() -> SecretCipher:
    return SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")


async def test_create_mints_id_and_rejects_duplicate_name() -> None:
    svc, _ = _service()
    public = await svc.create_provider(_body())
    assert public.provider_id == "p0"
    assert public.key_status == "not_set"
    with pytest.raises(HTTPException) as exc:
        await svc.create_provider(_body())
    assert exc.value.status_code == 409


async def test_update_by_id_and_404() -> None:
    svc, _ = _service()
    created = await svc.create_provider(_body())
    updated = await svc.update_provider(
        created.provider_id, _body(base_url="https://api.anthropic.com/v1")
    )
    assert updated.base_url == "https://api.anthropic.com/v1"
    with pytest.raises(ProviderNotFoundError):
        await svc.update_provider("ghost", _body())


async def test_set_key_encrypts_and_is_recoverable() -> None:
    svc, repo = _service(_cipher())
    created = await svc.create_provider(_body())
    public = await svc.set_api_key(created.provider_id, "sk-ant-secret")
    assert public.key_status == "set"
    # Ciphertext (not plaintext) persisted; decrypt round-trips.
    assert "sk-ant-secret" not in str(repo.store[created.provider_id])
    assert await svc.get_decrypted_key(created.provider_id) == "sk-ant-secret"


async def test_set_key_without_master_key_is_503_like() -> None:
    svc, _ = _service(cipher=None)
    created = await svc.create_provider(_body())
    with pytest.raises(KeyManagementUnavailableError):
        await svc.set_api_key(created.provider_id, "sk-x")


async def test_set_key_unknown_provider_raises() -> None:
    svc, _ = _service(_cipher())
    with pytest.raises(ProviderNotFoundError):
        await svc.set_api_key("ghost", "sk-x")


async def test_delete_provider() -> None:
    svc, _ = _service()
    created = await svc.create_provider(_body())
    assert await svc.delete_provider(created.provider_id) is True
    assert await svc.delete_provider(created.provider_id) is False
