# =============================================================================
# File: embedding_repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_repository.py
# Description: ArangoDB repositories for the platform EMBEDDING registry
#              (`embedding_providers` + `embedding_models`). Same sync-wrapped,
#              lock-guarded pattern as the chat registry repositories
#              (python-arango is not thread-safe). Store/read the RAW document
#              (provider ciphertext included) — the write-only-key projection
#              happens above, in the service/router, never here.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast

COLL_EMB_PROVIDERS = "embedding_providers"
COLL_EMB_MODELS = "embedding_models"

_T = TypeVar("_T")
_log = logging.getLogger("c8_llm.registry.embedding")


class _AsyncArango:
    """Shared sync→async, lock-guarded base (python-arango is not thread-safe)."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run(self, func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        async with self._get_lock():
            return await asyncio.to_thread(func, *args, **kwargs)

    def _ensure_one_sync(self, coll_name: str, unique_field: str) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if coll_name not in existing:
            self._db.create_collection(coll_name)
        coll = self._db.collection(coll_name)
        coll.add_index({"type": "persistent", "fields": ["enabled"]})
        try:
            coll.add_index(
                {"type": "persistent", "fields": [unique_field], "unique": True}
            )
        except Exception as exc:  # never crash-loop on dirty data
            _log.warning(
                "unique %s index on %s not created (dirty data?): %s",
                unique_field, coll_name, exc,
            )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class EmbeddingProviderStore(Protocol):
    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, provider_id: str) -> dict[str, Any] | None: ...
    async def get_by_name(self, name: str) -> dict[str, Any] | None: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def delete(self, provider_id: str) -> bool: ...


class EmbeddingProviderRepository(_AsyncArango):
    """Sync ArangoDB ops for `embedding_providers`, wrapped for async use."""

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_one_sync, COLL_EMB_PROVIDERS, "name")

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_EMB_PROVIDERS).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._run(self._upsert_sync, document)

    def _get_sync(self, provider_id: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._db.collection(COLL_EMB_PROVIDERS).get(provider_id),
        )

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_sync, provider_id)

    def _get_by_name_sync(self, name: str) -> dict[str, Any] | None:
        cursor = self._db.aql.execute(
            "FOR p IN @@coll FILTER p.name == @name LIMIT 1 RETURN p",
            bind_vars={"@coll": COLL_EMB_PROVIDERS, "name": name},
        )
        rows = list(cursor)
        return cast("dict[str, Any]", rows[0]) if rows else None

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return await self._run(self._get_by_name_sync, name)

    def _list_sync(self) -> list[dict[str, Any]]:
        return cast(
            "list[dict[str, Any]]",
            list(self._db.collection(COLL_EMB_PROVIDERS).all()),
        )

    async def list_all(self) -> list[dict[str, Any]]:
        return await self._run(self._list_sync)

    def _delete_sync(self, provider_id: str) -> bool:
        coll = self._db.collection(COLL_EMB_PROVIDERS)
        if coll.get(provider_id) is None:
            return False
        coll.delete(provider_id)
        return True

    async def delete(self, provider_id: str) -> bool:
        return await self._run(self._delete_sync, provider_id)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class EmbeddingModelStore(Protocol):
    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, model_id: str) -> dict[str, Any] | None: ...
    async def get_by_alias(self, alias: str) -> dict[str, Any] | None: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def list_for_provider(self, provider_id: str) -> list[dict[str, Any]]: ...
    async def delete(self, model_id: str) -> bool: ...


class EmbeddingModelRepository(_AsyncArango):
    """Sync ArangoDB ops for `embedding_models`, wrapped for async use."""

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_one_sync, COLL_EMB_MODELS, "alias")

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_EMB_MODELS).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._run(self._upsert_sync, document)

    def _get_sync(self, model_id: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._db.collection(COLL_EMB_MODELS).get(model_id),
        )

    async def get(self, model_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_sync, model_id)

    def _get_by_alias_sync(self, alias: str) -> dict[str, Any] | None:
        cursor = self._db.aql.execute(
            "FOR m IN @@coll FILTER m.alias == @alias LIMIT 1 RETURN m",
            bind_vars={"@coll": COLL_EMB_MODELS, "alias": alias},
        )
        rows = list(cursor)
        return cast("dict[str, Any]", rows[0]) if rows else None

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        return await self._run(self._get_by_alias_sync, alias)

    def _list_sync(self) -> list[dict[str, Any]]:
        return cast(
            "list[dict[str, Any]]", list(self._db.collection(COLL_EMB_MODELS).all())
        )

    async def list_all(self) -> list[dict[str, Any]]:
        return await self._run(self._list_sync)

    def _list_for_provider_sync(self, provider_id: str) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR m IN @@coll FILTER m.provider_id == @pid RETURN m",
            bind_vars={"@coll": COLL_EMB_MODELS, "pid": provider_id},
        )
        return cast("list[dict[str, Any]]", list(cursor))

    async def list_for_provider(self, provider_id: str) -> list[dict[str, Any]]:
        """Models served by one provider — used by the provider delete-guard."""
        return await self._run(self._list_for_provider_sync, provider_id)

    def _delete_sync(self, model_id: str) -> bool:
        coll = self._db.collection(COLL_EMB_MODELS)
        if coll.get(model_id) is None:
            return False
        coll.delete(model_id)
        return True

    async def delete(self, model_id: str) -> bool:
        return await self._run(self._delete_sync, model_id)
