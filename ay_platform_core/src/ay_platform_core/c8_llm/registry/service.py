# =============================================================================
# File: service.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/service.py
# Description: Business logic for the platform LLM MODEL registry. v2: models
#              are keyed by a stable `model_id` (minted on create); `alias` is a
#              mutable, unique attribute; the endpoint URL + key live on the
#              referenced provider, so this service holds NO secret. Tenant/
#              project references use `model_id` and survive any attribute edit.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import HTTPException, status

from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    LLMRegistryEntry,
    LLMRegistryPublic,
)
from ay_platform_core.c8_llm.registry.repository import RegistryStore


class ModelNotFoundError(LookupError):
    """Raised when an operation targets a model_id absent from the registry."""


def _default_clock() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class LLMRegistryService:
    """Application service for the platform LLM model registry."""

    def __init__(
        self,
        repo: RegistryStore,
        *,
        clock: Callable[[], str] = _default_clock,
        id_factory: Callable[[], str] = _new_id,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._new_id = id_factory

    # ---- Reads ------------------------------------------------------------

    async def list_models(self) -> list[LLMRegistryPublic]:
        docs = await self._repo.list_all()
        return [LLMRegistryEntry.from_document(d).to_public() for d in docs]

    async def get_model(self, model_id: str) -> LLMRegistryPublic | None:
        entry = await self._get_entry(model_id)
        return entry.to_public() if entry is not None else None

    async def get_model_by_alias(self, alias: str) -> LLMRegistryPublic | None:
        doc = await self._repo.get_by_alias(alias)
        return None if doc is None else LLMRegistryEntry.from_document(doc).to_public()

    async def _get_entry(self, model_id: str) -> LLMRegistryEntry | None:
        doc = await self._repo.get(model_id)
        return None if doc is None else LLMRegistryEntry.from_document(doc)

    # ---- Writes -----------------------------------------------------------

    async def create_model(self, body: LLMModelUpsert) -> LLMRegistryPublic:
        """Mint a model_id and persist. 409 on a duplicate alias."""
        await self._guard_alias(body.alias, model_id=None)
        entry = self._entry_from(self._new_id(), body)
        await self._repo.upsert(entry.to_document())
        return entry.to_public()

    async def update_model(
        self, model_id: str, body: LLMModelUpsert
    ) -> LLMRegistryPublic:
        """Update every attribute (including alias) of an existing model. The
        model_id (and thus all tenant/project references) is unchanged. 404 on
        unknown id, 409 on an alias clash with another model."""
        existing = await self._get_entry(model_id)
        if existing is None:
            raise ModelNotFoundError(model_id)
        await self._guard_alias(body.alias, model_id=model_id)
        entry = self._entry_from(model_id, body)
        await self._repo.upsert(entry.to_document())
        return entry.to_public()

    async def delete_model(self, model_id: str) -> bool:
        """Delete a model by id. False if it did not exist."""
        return await self._repo.delete(model_id)

    # ---- Helpers ----------------------------------------------------------

    def _entry_from(self, model_id: str, body: LLMModelUpsert) -> LLMRegistryEntry:
        return LLMRegistryEntry(
            model_id=model_id,
            alias=body.alias,
            provider_id=body.provider_id,
            upstream_model=body.upstream_model,
            capabilities=body.capabilities,
            provider_cost_in_per_1m=body.provider_cost_in_per_1m,
            provider_cost_out_per_1m=body.provider_cost_out_per_1m,
            default_model_quality=body.default_model_quality,
            enabled=body.enabled,
            effective_from=self._clock(),
        )

    async def _guard_alias(self, alias: str, *, model_id: str | None) -> None:
        clash = await self._repo.get_by_alias(alias)
        if clash is not None and clash.get("_key") != model_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"model alias {alias!r} already exists",
            )
