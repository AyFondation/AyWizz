# =============================================================================
# File: embedding_catalog_repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_catalog_repository.py
# Description: ArangoDB repositories for the per-tenant EMBEDDING catalogue
#              (`embedding_catalog`, keyed `{tenant}:{model_id}`) and the per-
#              project selection (`embedding_project_selection`, keyed
#              `{tenant}:{project}`). Same sync-wrapped, lock-guarded pattern as
#              the registry repos. `list_using_model` backs the DELETE-GUARD
#              (a model in use by a project cannot be removed).
# =============================================================================

from __future__ import annotations

from typing import Any, Protocol, cast

from ay_platform_core.c8_llm.registry.embedding_repository import _AsyncArango

COLL_EMB_CATALOG = "embedding_catalog"
COLL_EMB_PROJECT = "embedding_project_selection"


class EmbeddingCatalogStore(Protocol):
    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]: ...
    async def list_for_model(self, model_id: str) -> list[dict[str, Any]]: ...
    async def delete(self, key: str) -> bool: ...


class EmbeddingProjectStore(Protocol):
    async def upsert(self, document: dict[str, Any]) -> None: ...
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def list_using_model(self, model_id: str) -> list[dict[str, Any]]: ...
    async def delete(self, key: str) -> bool: ...


class EmbeddingCatalogRepository(_AsyncArango):
    """`embedding_catalog` — which registry models a tenant exposes + a default."""

    def _ensure_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_EMB_CATALOG not in existing:
            self._db.create_collection(COLL_EMB_CATALOG)
        self._db.collection(COLL_EMB_CATALOG).add_index(
            {"type": "persistent", "fields": ["tenant_id"]}
        )
        self._db.collection(COLL_EMB_CATALOG).add_index(
            {"type": "persistent", "fields": ["model_id"]}
        )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_sync)

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_EMB_CATALOG).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._run(self._upsert_sync, document)

    def _get_sync(self, key: str) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self._db.collection(COLL_EMB_CATALOG).get(key))

    async def get(self, key: str) -> dict[str, Any] | None:
        return await self._run(self._get_sync, key)

    def _by_field_sync(self, field: str, value: str) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            f"FOR c IN @@coll FILTER c.{field} == @v RETURN c",
            bind_vars={"@coll": COLL_EMB_CATALOG, "v": value},
        )
        return cast("list[dict[str, Any]]", list(cursor))

    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        return await self._run(self._by_field_sync, "tenant_id", tenant_id)

    async def list_for_model(self, model_id: str) -> list[dict[str, Any]]:
        """Every tenant catalogue row referencing a model — the platform-level
        model delete-guard reads this."""
        return await self._run(self._by_field_sync, "model_id", model_id)

    def _delete_sync(self, key: str) -> bool:
        coll = self._db.collection(COLL_EMB_CATALOG)
        if coll.get(key) is None:
            return False
        coll.delete(key)
        return True

    async def delete(self, key: str) -> bool:
        return await self._run(self._delete_sync, key)


class EmbeddingProjectRepository(_AsyncArango):
    """`embedding_project_selection` — a project's single chosen embedding."""

    def _ensure_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_EMB_PROJECT not in existing:
            self._db.create_collection(COLL_EMB_PROJECT)
        self._db.collection(COLL_EMB_PROJECT).add_index(
            {"type": "persistent", "fields": ["model_id"]}
        )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_sync)

    def _upsert_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_EMB_PROJECT).insert(document, overwrite=True)

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._run(self._upsert_sync, document)

    def _get_sync(self, key: str) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self._db.collection(COLL_EMB_PROJECT).get(key))

    async def get(self, key: str) -> dict[str, Any] | None:
        return await self._run(self._get_sync, key)

    def _using_model_sync(self, model_id: str) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR s IN @@coll FILTER s.model_id == @mid RETURN s",
            bind_vars={"@coll": COLL_EMB_PROJECT, "mid": model_id},
        )
        return cast("list[dict[str, Any]]", list(cursor))

    async def list_using_model(self, model_id: str) -> list[dict[str, Any]]:
        """Projects that SELECTED a given model — backs the DELETE-GUARD."""
        return await self._run(self._using_model_sync, model_id)

    def _delete_sync(self, key: str) -> bool:
        coll = self._db.collection(COLL_EMB_PROJECT)
        if coll.get(key) is None:
            return False
        coll.delete(key)
        return True

    async def delete(self, key: str) -> bool:
        return await self._run(self._delete_sync, key)
