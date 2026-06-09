# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/repository.py
# Description: ArangoDB repository for the platform LLM registry collection
#              (`llm_registry`, `_key = model_alias`). Same sync-wrapped,
#              lock-guarded pattern as C7's MemoryRepository (python-arango is
#              not thread-safe). Stores/reads the raw document (ciphertext
#              included) — the WRITE-ONLY-KEY projection happens above, in the
#              service/router, never here.
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast

COLL_REGISTRY = "llm_registry"

_T = TypeVar("_T")


class RegistryStore(Protocol):
    """The storage surface the registry service + seeder depend on. Decouples
    them from the concrete Arango repository so they can be exercised against
    an in-memory fake (structural typing, no mocking of the unit under test).
    `_key` is the stable `model_id`; `get_by_alias` resolves the mutable alias
    (used by the per-call injector / agent-route path)."""

    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, model_id: str) -> dict[str, Any] | None: ...
    async def get_by_alias(self, alias: str) -> dict[str, Any] | None: ...
    async def list_all(self) -> list[dict[str, Any]]: ...
    async def delete(self, model_id: str) -> bool: ...


class LLMRegistryRepository:
    """Sync ArangoDB operations for `llm_registry`, wrapped for async use."""

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

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_REGISTRY not in existing:
            self._db.create_collection(COLL_REGISTRY)
        coll = self._db.collection(COLL_REGISTRY)
        coll.add_index({"type": "persistent", "fields": ["enabled"]})
        # Alias is a UNIQUE, mutable secondary key (the human handle); model_id
        # (`_key`) is the stable reference. The unique index guards double-
        # aliasing — but creating it over a store that ALREADY holds duplicate
        # (or legacy alias-less) docs would crash the boot. A pod must never
        # crash-loop on dirty data: warn + continue (the service still serves;
        # an operator cleans up + the index is re-attempted on the next boot).
        try:
            coll.add_index({"type": "persistent", "fields": ["alias"], "unique": True})
        except Exception as exc:
            import logging  # noqa: PLC0415 — cold path

            logging.getLogger("c8_llm.registry").warning(
                "unique alias index not created (existing dirty data?): %s", exc
            )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_REGISTRY).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        """Insert or replace a registry document (keyed by `_key`)."""
        await self._run(self._upsert_sync, document)

    def _get_sync(self, model_id: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._db.collection(COLL_REGISTRY).get(model_id),
        )

    async def get(self, model_id: str) -> dict[str, Any] | None:
        """Return the raw document by stable id (`_key`) or None."""
        return await self._run(self._get_sync, model_id)

    def _get_by_alias_sync(self, alias: str) -> dict[str, Any] | None:
        cursor = self._db.aql.execute(
            "FOR m IN @@coll FILTER m.alias == @alias LIMIT 1 RETURN m",
            bind_vars={"@coll": COLL_REGISTRY, "alias": alias},
        )
        rows = list(cursor)
        return cast("dict[str, Any]", rows[0]) if rows else None

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        """Resolve the mutable alias to a raw document (or None)."""
        return await self._run(self._get_by_alias_sync, alias)

    def _list_sync(self) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR m IN @@coll SORT m.alias ASC RETURN m",
            bind_vars={"@coll": COLL_REGISTRY},
        )
        return [cast("dict[str, Any]", d) for d in cursor]

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all registry documents, sorted by alias (stable order)."""
        return await self._run(self._list_sync)

    def _delete_sync(self, model_id: str) -> bool:
        coll = self._db.collection(COLL_REGISTRY)
        if not coll.has(model_id):
            return False
        coll.delete(model_id)
        return True

    async def delete(self, model_id: str) -> bool:
        """Delete a registry document by id. False when it did not exist."""
        return await self._run(self._delete_sync, model_id)
