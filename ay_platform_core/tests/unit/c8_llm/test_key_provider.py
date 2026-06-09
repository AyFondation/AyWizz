# =============================================================================
# File: test_key_provider.py
# Path: ay_platform_core/tests/unit/c8_llm/test_key_provider.py
# Description: Unit tests for the per-call CallTarget resolver (v2). Resolves an
#              alias → model → provider and returns the rewritten litellm model
#              (`wire_format/upstream`), the provider's mandatory api_base, and
#              the decrypted provider key. Graceful on unknown alias / dangling
#              provider. Best-effort by construction.
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c8_llm.registry.key_provider import (
    RegistryKeyProvider,
    build_registry_key_provider,
)
from ay_platform_core.crypto.secret_cipher import SecretCipher

pytestmark = pytest.mark.asyncio


def _aad(pid: str) -> str:
    return f"llm_provider:{pid}:api_key"


class _Reg:
    def __init__(self, by_alias: dict[str, dict[str, Any]]) -> None:
        self._by_alias = by_alias

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        return self._by_alias.get(alias)

    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, model_id: str) -> dict[str, Any] | None:
        return None

    async def list_all(self) -> list[dict[str, Any]]:
        return []

    async def delete(self, model_id: str) -> bool:
        return False


class _Prov:
    def __init__(self, by_id: dict[str, dict[str, Any]]) -> None:
        self._by_id = by_id

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return self._by_id.get(provider_id)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return None

    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def list_all(self) -> list[dict[str, Any]]:
        return []

    async def delete(self, provider_id: str) -> bool:
        return False


def _provider_doc(cipher: SecretCipher | None = None, key: str | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_key": "p1",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "wire_format": "anthropic",
        "api_key_ciphertext": None,
    }
    if cipher is not None and key is not None:
        doc["api_key_ciphertext"] = cipher.encrypt(key, aad=_aad("p1"))
    return doc


def _model_doc(provider_id: str = "p1") -> dict[str, Any]:
    return {
        "_key": "m1",
        "alias": "haiku",
        "provider_id": provider_id,
        "upstream_model": "claude-haiku-4-5",
    }


async def test_resolves_model_rewrite_base_and_key() -> None:
    cipher = SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")
    reg = _Reg({"haiku": _model_doc()})
    prov = _Prov({"p1": _provider_doc(cipher, "sk-ant-secret")})
    target = await RegistryKeyProvider(reg, prov, cipher)("haiku")
    assert target is not None
    assert target.model == "anthropic/claude-haiku-4-5"  # wire_format/upstream
    assert target.api_base == "https://api.anthropic.com"
    assert target.api_key == "sk-ant-secret"


async def test_no_key_yields_none_key_but_still_rewrites() -> None:
    reg = _Reg({"haiku": _model_doc()})
    prov = _Prov({"p1": _provider_doc()})  # no key
    target = await RegistryKeyProvider(reg, prov, None)("haiku")
    assert target is not None
    assert target.model == "anthropic/claude-haiku-4-5"
    assert target.api_base == "https://api.anthropic.com"
    assert target.api_key is None


async def test_unknown_alias_returns_none() -> None:
    assert await RegistryKeyProvider(_Reg({}), _Prov({}), None)("ghost") is None


async def test_dangling_provider_returns_none() -> None:
    reg = _Reg({"haiku": _model_doc(provider_id="missing")})
    assert await RegistryKeyProvider(reg, _Prov({}), None)("haiku") is None


async def test_builder_always_returns_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AY_SECRET_MASTER_KEY", raising=False)
    monkeypatch.delenv("AY_SECRET_MASTER_KEYS", raising=False)
    assert isinstance(build_registry_key_provider(db=object()), RegistryKeyProvider)
