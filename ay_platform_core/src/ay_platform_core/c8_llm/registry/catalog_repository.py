# =============================================================================
# File: catalog_repository.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/catalog_repository.py
# Description: ArangoDB repository for the per-tenant LLM catalogue
#              (`tenant_llm_catalog`, `_key = {tenant_id}:{model_id}`).
#              Same sync-wrapped, lock-guarded pattern as the registry repo.
#              Carries no secret — catalogue rows are pure (tenant, alias,
#              enable flag, optional rate-card).
#              v2 (800 v10): a per-tenant `initialized` marker
#              (`tenant_llm_catalog_meta`, `_key = tenant_id`) records that a
#              tenant's catalogue has been lazily materialised from the platform
#              registry (opt-out default), so an admin who removes every model
#              is NOT re-populated on the next read.
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast

COLL_CATALOG = "tenant_llm_catalog"
COLL_PROJECT_MODELS = "project_llm_models"
COLL_CATALOG_META = "tenant_llm_catalog_meta"

_T = TypeVar("_T")


class CatalogStore(Protocol):
    """Storage surface the catalogue service depends on (decouples from Arango
    so the service can be unit-tested against an in-memory fake)."""

    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, tenant_id: str, model_id: str) -> dict[str, Any] | None: ...
    async def delete(self, tenant_id: str, model_id: str) -> bool: ...
    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]: ...
    async def is_initialized(self, tenant_id: str) -> bool: ...
    async def mark_initialized(self, tenant_id: str) -> None: ...
    async def get_project_models(
        self, tenant_id: str, project_id: str
    ) -> dict[str, Any] | None: ...
    async def set_project_models(self, document: dict[str, Any]) -> None: ...


class TenantCatalogRepository:
    """Sync ArangoDB operations for `tenant_llm_catalog`, wrapped for async."""

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
        if COLL_CATALOG not in existing:
            self._db.create_collection(COLL_CATALOG)
        self._db.collection(COLL_CATALOG).add_index(
            {"type": "persistent", "fields": ["tenant_id", "enabled"]}
        )
        if COLL_PROJECT_MODELS not in existing:
            self._db.create_collection(COLL_PROJECT_MODELS)
        if COLL_CATALOG_META not in existing:
            self._db.create_collection(COLL_CATALOG_META)

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    # ---- Per-tenant initialisation marker (opt-out lazy materialisation) --

    def _is_initialized_sync(self, tenant_id: str) -> bool:
        return bool(self._db.collection(COLL_CATALOG_META).has(tenant_id))

    async def is_initialized(self, tenant_id: str) -> bool:
        return await self._run(self._is_initialized_sync, tenant_id)

    def _mark_initialized_sync(self, tenant_id: str) -> None:
        self._db.collection(COLL_CATALOG_META).insert(
            {"_key": tenant_id, "initialized": True}, overwrite=True
        )

    async def mark_initialized(self, tenant_id: str) -> None:
        await self._run(self._mark_initialized_sync, tenant_id)

    # ---- Per-project model associations (`{tenant}:{project}`) ------------

    @staticmethod
    def _pm_key(tenant_id: str, project_id: str) -> str:
        return f"{tenant_id}:{project_id}"

    def _get_project_models_sync(
        self, tenant_id: str, project_id: str
    ) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._db.collection(COLL_PROJECT_MODELS).get(
                self._pm_key(tenant_id, project_id)
            ),
        )

    async def get_project_models(
        self, tenant_id: str, project_id: str
    ) -> dict[str, Any] | None:
        return await self._run(self._get_project_models_sync, tenant_id, project_id)

    def _set_project_models_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_PROJECT_MODELS).insert(document, overwrite=True)

    async def set_project_models(self, document: dict[str, Any]) -> None:
        await self._run(self._set_project_models_sync, document)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _key(tenant_id: str, model_id: str) -> str:
        return f"{tenant_id}:{model_id}"

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_CATALOG).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._run(self._upsert_sync, document)

    def _get_sync(self, tenant_id: str, model_id: str) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._db.collection(COLL_CATALOG).get(self._key(tenant_id, model_id)),
        )

    async def get(self, tenant_id: str, model_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_sync, tenant_id, model_id)

    def _delete_sync(self, tenant_id: str, model_id: str) -> bool:
        coll = self._db.collection(COLL_CATALOG)
        key = self._key(tenant_id, model_id)
        if not coll.has(key):
            return False
        coll.delete(key)
        return True

    async def delete(self, tenant_id: str, model_id: str) -> bool:
        """Delete a catalogue row. Returns False when it did not exist."""
        return await self._run(self._delete_sync, tenant_id, model_id)

    def _list_sync(self, tenant_id: str) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR c IN @@coll FILTER c.tenant_id == @tid SORT c.model_id ASC RETURN c",
            bind_vars={"@coll": COLL_CATALOG, "tid": tenant_id},
        )
        return [cast("dict[str, Any]", d) for d in cursor]

    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        return await self._run(self._list_sync, tenant_id)
