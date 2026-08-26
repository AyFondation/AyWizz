# =============================================================================
# File: embedding_service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_service.py
# Description: Business logic for the platform EMBEDDING registry (D-011,
#              parallel to the chat provider + model services). Owns the SINGLE
#              place an embedding provider's API key is encrypted (on write) /
#              decrypted (for C7). Mints stable ids, enforces name/alias
#              uniqueness, serves write-only public projections (never a key),
#              and guards a provider delete while models reference it. The
#              in-use-by-a-PROJECT delete-guard lives one layer up (tenant
#              catalogue, B3). Cipher-optional: reads work without a master key;
#              key WRITES raise.
#
# @relation implements:R-400-226
# @relation implements:R-400-229
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingModelEntry,
    EmbeddingModelPublic,
    EmbeddingModelUpsert,
    EmbeddingProviderEntry,
    EmbeddingProviderPublic,
    EmbeddingProviderUpsert,
)
from ay_platform_core.c8_llm.registry.embedding_repository import (
    EmbeddingModelStore,
    EmbeddingProviderStore,
)
from ay_platform_core.crypto.secret_cipher import SecretCipher, masked_suffix


class EmbeddingProviderNotFoundError(LookupError):
    """Raised when an operation targets an unknown embedding provider_id."""


class EmbeddingModelNotFoundError(LookupError):
    """Raised when an operation targets an unknown embedding model_id."""


class KeyManagementUnavailableError(RuntimeError):
    """Raised on a key encrypt/decrypt op without a configured master key."""


def _aad(provider_id: str) -> str:
    return f"embedding_provider:{provider_id}:api_key"


def _clock() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class EmbeddingProviderService:
    def __init__(
        self,
        repo: EmbeddingProviderStore,
        model_repo: EmbeddingModelStore,
        cipher: SecretCipher | None = None,
        *,
        clock: Callable[[], str] = _clock,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self._repo = repo
        self._model_repo = model_repo
        self._cipher = cipher
        self._clock = clock
        self._new_id = id_factory

    async def list_providers(self) -> list[EmbeddingProviderPublic]:
        docs = await self._repo.list_all()
        return [EmbeddingProviderEntry.from_document(d).to_public() for d in docs]

    async def _get_entry(self, provider_id: str) -> EmbeddingProviderEntry | None:
        doc = await self._repo.get(provider_id)
        return None if doc is None else EmbeddingProviderEntry.from_document(doc)

    async def create_provider(
        self, body: EmbeddingProviderUpsert
    ) -> EmbeddingProviderPublic:
        if await self._repo.get_by_name(body.name) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"embedding provider name {body.name!r} already exists",
            )
        entry = EmbeddingProviderEntry(
            provider_id=self._new_id(),
            name=body.name,
            adapter=body.adapter,
            base_url=body.base_url,
            effective_from=self._clock(),
        )
        await self._repo.upsert(entry.to_document())
        return entry.to_public()

    async def update_provider(
        self, provider_id: str, body: EmbeddingProviderUpsert
    ) -> EmbeddingProviderPublic:
        existing = await self._get_entry(provider_id)
        if existing is None:
            raise EmbeddingProviderNotFoundError(provider_id)
        clash = await self._repo.get_by_name(body.name)
        if clash is not None and clash.get("_key") != provider_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"embedding provider name {body.name!r} already exists",
            )
        updated = existing.model_copy(
            update={
                "name": body.name,
                "adapter": body.adapter,
                "base_url": body.base_url,
                "effective_from": self._clock(),
            }
        )
        await self._repo.upsert(updated.to_document())
        return updated.to_public()

    async def delete_provider(self, provider_id: str) -> bool:
        """Delete a provider. 409 while any model still references it (a
        dangling model would have no endpoint/key)."""
        models = await self._model_repo.list_for_provider(provider_id)
        if models:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"provider {provider_id} still has {len(models)} model(s); "
                    "delete those first"
                ),
            )
        return await self._repo.delete(provider_id)

    async def set_api_key(
        self, provider_id: str, plaintext: str
    ) -> EmbeddingProviderPublic:
        if self._cipher is None:
            raise KeyManagementUnavailableError("no master key configured")
        entry = await self._get_entry(provider_id)
        if entry is None:
            raise EmbeddingProviderNotFoundError(provider_id)
        updated = entry.model_copy(
            update={
                "api_key_ciphertext": self._cipher.encrypt(plaintext, aad=_aad(provider_id)),
                "api_key_hint": masked_suffix(plaintext),
                "effective_from": self._clock(),
            }
        )
        await self._repo.upsert(updated.to_document())
        return updated.to_public()

    async def get_decrypted_key(self, provider_id: str) -> str | None:
        entry = await self._get_entry(provider_id)
        if entry is None or entry.api_key_ciphertext is None:
            return None
        if self._cipher is None:
            raise KeyManagementUnavailableError("no master key configured")
        return self._cipher.decrypt(entry.api_key_ciphertext, aad=_aad(provider_id))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class EmbeddingModelService:
    def __init__(
        self,
        repo: EmbeddingModelStore,
        provider_repo: EmbeddingProviderStore,
        project_repo: Any | None = None,
        reembed_notifier: Any | None = None,
        *,
        clock: Callable[[], str] = _clock,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self._repo = repo
        self._provider_repo = provider_repo
        # Optional project-selection store: when wired, DELETE is GUARDED — a
        # model still selected by a project cannot be deleted (protects the
        # project's index from losing its embedder). Also used to enumerate the
        # projects to re-embed when the model changes.
        self._project_repo = project_repo
        # Optional ReembedNotifier: when wired, a vector-affecting model edit
        # triggers a best-effort re-embed of every project using it (R-400-229).
        self._reembed = reembed_notifier
        self._clock = clock
        self._new_id = id_factory

    async def list_models(self) -> list[EmbeddingModelPublic]:
        docs = await self._repo.list_all()
        return [EmbeddingModelEntry.from_document(d).to_public() for d in docs]

    async def _get_entry(self, model_id: str) -> EmbeddingModelEntry | None:
        doc = await self._repo.get(model_id)
        return None if doc is None else EmbeddingModelEntry.from_document(doc)

    async def _require_provider(self, provider_id: str) -> None:
        if await self._provider_repo.get(provider_id) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown embedding provider id: {provider_id}",
            )

    async def create_model(self, body: EmbeddingModelUpsert) -> EmbeddingModelPublic:
        await self._require_provider(body.provider_id)
        if await self._repo.get_by_alias(body.alias) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"embedding model alias {body.alias!r} already exists",
            )
        entry = EmbeddingModelEntry(
            model_id=self._new_id(),
            alias=body.alias,
            provider_id=body.provider_id,
            upstream_model=body.upstream_model,
            dimension=body.dimension,
            enabled=body.enabled,
            effective_from=self._clock(),
        )
        await self._repo.upsert(entry.to_document())
        return entry.to_public()

    async def update_model(
        self, model_id: str, body: EmbeddingModelUpsert
    ) -> EmbeddingModelPublic:
        existing = await self._get_entry(model_id)
        if existing is None:
            raise EmbeddingModelNotFoundError(model_id)
        await self._require_provider(body.provider_id)
        clash = await self._repo.get_by_alias(body.alias)
        if clash is not None and clash.get("_key") != model_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"embedding model alias {body.alias!r} already exists",
            )
        updated = existing.model_copy(
            update={
                "alias": body.alias,
                "provider_id": body.provider_id,
                "upstream_model": body.upstream_model,
                "dimension": body.dimension,
                "enabled": body.enabled,
                "effective_from": self._clock(),
            }
        )
        await self._repo.upsert(updated.to_document())
        # A change to the upstream model, dimension, or provider changes the
        # VECTORS this model produces → every project using it needs a
        # re-embed. Alias/enabled edits don't affect vectors, so they don't.
        vector_affecting = (
            existing.upstream_model != body.upstream_model
            or existing.dimension != body.dimension
            or existing.provider_id != body.provider_id
        )
        if vector_affecting:
            await self._trigger_reembed_for_model(model_id)
        return updated.to_public()

    async def _trigger_reembed_for_model(self, model_id: str) -> None:
        """Best-effort: notify a re-embed for every project selecting this
        model. No-op without a notifier or a project store wired."""
        if self._reembed is None or self._project_repo is None:
            return
        for sel in await self._project_repo.list_using_model(model_id):
            tenant_id = str(sel.get("tenant_id") or "")
            project_id = str(sel.get("project_id") or "")
            if tenant_id and project_id:
                await self._reembed.notify(
                    tenant_id=tenant_id, project_id=project_id, model_id=model_id
                )

    async def delete_model(self, model_id: str) -> bool:
        """Platform-level delete. GUARDED when a project store is wired: 409
        while any project still selects this model (protects the project index)."""
        if self._project_repo is not None:
            users = await self._project_repo.list_using_model(model_id)
            if users:
                projects = [str(s.get("project_id")) for s in users]
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"embedding model {model_id} is selected by "
                        f"{len(projects)} project(s): {projects}; reassign them first"
                    ),
                )
        return await self._repo.delete(model_id)
