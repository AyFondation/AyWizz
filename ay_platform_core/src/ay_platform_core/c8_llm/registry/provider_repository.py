# =============================================================================
# File: provider_repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/provider_repository.py
# Description: ArangoDB repository for the LLM provider collection
#              (`llm_providers`, `_key = provider_id`). Same sync-wrapped,
#              lock-guarded pattern as the model registry. Stores the raw doc
#              (ciphertext included) — the write-only-key projection happens in
#              the service/router. `name` is a unique secondary key.
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast

COLL_PROVIDERS = "llm_providers"

_T = TypeVar("_T")


class ProviderStore(Protocol):
    """Storage surface the provider service + seeder depend on."""

    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, provider_id: str) -> dict[str, Any] | None: ...
    async def get_by_name(self, name: str) -> dict[str, Any] | None: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def delete(self, provider_id: str) -> bool: ...


class LLMProviderRepository:
    """Sync ArangoDB operations for `llm_providers`, wrapped for async use."""

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

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_PROVIDERS not in existing:
            self._db.create_collection(COLL_PROVIDERS)
        # Unique on `name`; resilient against pre-existing dirty data so the pod
        # never crash-loops (warn + continue, re-attempted on the next boot).
        try:
            self._db.collection(COLL_PROVIDERS).add_index(
                {"type": "persistent", "fields": ["name"], "unique": True}
            )
        except Exception as exc:
            import logging  # noqa: PLC0415 — cold path

            logging.getLogger("c8_llm.provider").warning(
                "unique name index not created (existing dirty data?): %s", exc
            )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_PROVIDERS).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._run(self._upsert_sync, document)

    def _get_sync(self, provider_id: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None", self._db.collection(COLL_PROVIDERS).get(provider_id)
        )

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_sync, provider_id)

    def _get_by_name_sync(self, name: str) -> dict[str, Any] | None:
        cursor = self._db.aql.execute(
            "FOR p IN @@coll FILTER p.name == @name LIMIT 1 RETURN p",
            bind_vars={"@coll": COLL_PROVIDERS, "name": name},
        )
        rows = list(cursor)
        return cast("dict[str, Any]", rows[0]) if rows else None

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return await self._run(self._get_by_name_sync, name)

    def _list_sync(self) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR p IN @@coll SORT p.name ASC RETURN p",
            bind_vars={"@coll": COLL_PROVIDERS},
        )
        return [cast("dict[str, Any]", d) for d in cursor]

    async def list_all(self) -> list[dict[str, Any]]:
        return await self._run(self._list_sync)

    def _delete_sync(self, provider_id: str) -> bool:
        coll = self._db.collection(COLL_PROVIDERS)
        if not coll.has(provider_id):
            return False
        coll.delete(provider_id)
        return True

    async def delete(self, provider_id: str) -> bool:
        return await self._run(self._delete_sync, provider_id)
