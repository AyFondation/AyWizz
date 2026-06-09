# =============================================================================
# File: provider_service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/provider_service.py
# Description: Business logic for the LLM provider registry. Owns the SINGLE
#              place a provider's API key is encrypted (on write) / decrypted
#              (for per-call injection). Mints the stable `provider_id`, enforces
#              `name` uniqueness, and serves the write-only `LLMProviderPublic`
#              projection (never the key). Cipher-optional: read/list/resolve
#              work without a master key; key WRITES raise.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException, status

from ay_platform_core.c8_llm.registry.provider_models import (
    LLMProviderEntry,
    LLMProviderPublic,
    LLMProviderUpsert,
)
from ay_platform_core.c8_llm.registry.provider_repository import ProviderStore
from ay_platform_core.crypto.secret_cipher import SecretCipher, masked_suffix


class ProviderNotFoundError(LookupError):
    """Raised when an operation targets a provider_id absent from the registry."""


class KeyManagementUnavailableError(RuntimeError):
    """Raised on a key encrypt/decrypt op without a configured master key."""


def _aad(provider_id: str) -> str:
    return f"llm_provider:{provider_id}:api_key"


def _default_clock() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class LLMProviderService:
    """Application service for the LLM provider registry."""

    def __init__(
        self,
        repo: ProviderStore,
        cipher: SecretCipher | None = None,
        *,
        clock: Callable[[], str] = _default_clock,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._clock = clock
        self._new_id = id_factory

    async def list_providers(self) -> list[LLMProviderPublic]:
        docs = await self._repo.list_all()
        return [LLMProviderEntry.from_document(d).to_public() for d in docs]

    async def get_provider(self, provider_id: str) -> LLMProviderPublic | None:
        entry = await self._get_entry(provider_id)
        return entry.to_public() if entry is not None else None

    async def _get_entry(self, provider_id: str) -> LLMProviderEntry | None:
        doc = await self._repo.get(provider_id)
        return None if doc is None else LLMProviderEntry.from_document(doc)

    async def create_provider(self, body: LLMProviderUpsert) -> LLMProviderPublic:
        """Mint a provider_id and persist. 409 on a duplicate name."""
        clash = await self._repo.get_by_name(body.name)
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"provider name {body.name!r} already exists",
            )
        entry = LLMProviderEntry(
            provider_id=self._new_id(),
            name=body.name,
            base_url=body.base_url,
            wire_format=body.wire_format,
            effective_from=self._clock(),
        )
        await self._repo.upsert(entry.to_document())
        return entry.to_public()

    async def update_provider(
        self, provider_id: str, body: LLMProviderUpsert
    ) -> LLMProviderPublic:
        """Update metadata (key preserved). 404 unknown id, 409 name clash."""
        existing = await self._get_entry(provider_id)
        if existing is None:
            raise ProviderNotFoundError(provider_id)
        clash = await self._repo.get_by_name(body.name)
        if clash is not None and clash.get("_key") != provider_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"provider name {body.name!r} already exists",
            )
        updated = existing.model_copy(
            update={
                "name": body.name,
                "base_url": body.base_url,
                "wire_format": body.wire_format,
                "effective_from": self._clock(),
            }
        )
        await self._repo.upsert(updated.to_document())
        return updated.to_public()

    async def delete_provider(self, provider_id: str) -> bool:
        return await self._repo.delete(provider_id)

    async def set_api_key(self, provider_id: str, plaintext: str) -> LLMProviderPublic:
        """Encrypt + store the provider key. 503 without a master key, 404 when
        the provider is unknown."""
        if self._cipher is None:
            raise KeyManagementUnavailableError("no master key configured")
        entry = await self._get_entry(provider_id)
        if entry is None:
            raise ProviderNotFoundError(provider_id)
        ciphertext = self._cipher.encrypt(plaintext, aad=_aad(provider_id))
        updated = entry.model_copy(
            update={
                "api_key_ciphertext": ciphertext,
                "api_key_hint": masked_suffix(plaintext),
                "effective_from": self._clock(),
            }
        )
        await self._repo.upsert(updated.to_document())
        return updated.to_public()

    async def get_decrypted_key(self, provider_id: str) -> str | None:
        """Decrypt the provider key, or None when unset. Used by the per-call
        injector. Never logged."""
        entry = await self._get_entry(provider_id)
        if entry is None or entry.api_key_ciphertext is None:
            return None
        if self._cipher is None:
            raise KeyManagementUnavailableError("no master key configured")
        return self._cipher.decrypt(entry.api_key_ciphertext, aad=_aad(provider_id))
