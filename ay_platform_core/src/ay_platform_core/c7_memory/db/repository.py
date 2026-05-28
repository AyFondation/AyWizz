# =============================================================================
# File: repository.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c7_memory/db/repository.py
# Description: ArangoDB repository for C7 — memory_chunks, memory_sources,
#              memory_links (E-400-002, E-400-003). Reuses the lock pattern
#              from C5/C4 since python-arango is not thread-safe.
#              Cosine similarity is implemented client-side in Python
#              rather than as an AQL UDF (AQL UDFs add a deployment step;
#              the scan is bounded by a pre-filter and fits in-memory).
#
#              v2 (Phase F.2): adds `fetch_chunks_for_source_ids` —
#              targeted fetch by explicit source_ids, used by the KG
#              expansion path to retrieve chunks that the bounded
#              `scan_chunks` may have cut off. Same tenant/project/
#              index/model gating as scan; no scan_cap (caller bounds
#              by source_ids cardinality).
#
#              v3 (V2 #3-A.b, R-400-202): adds the BM25 LEXICAL arm of
#              hybrid retrieval — an ArangoSearch view (`memory_chunks_search`)
#              over `memory_chunks.content` (built-in `text_en` analyzer) +
#              `lexical_search(...)`. The dense (cosine) arm of R-400-011 is
#              unchanged ; the service fuses the two by RRF.
#
# @relation implements:R-400-010
# @relation implements:R-400-011
# @relation implements:R-400-202
# @relation implements:E-400-002
# @relation implements:E-400-003
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar, cast

from arango import ArangoClient  # type: ignore[attr-defined]

COLL_CHUNKS = "memory_chunks"
COLL_SOURCES = "memory_sources"
COLL_LINKS = "memory_links"
# ArangoSearch view backing the BM25 lexical arm (R-400-202).
VIEW_CHUNKS = "memory_chunks_search"

_T = TypeVar("_T")


class MemoryRepository:
    """Sync ArangoDB operations wrapped for async use via asyncio.to_thread."""

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
        for name in (COLL_CHUNKS, COLL_SOURCES):
            if name not in existing:
                self._db.create_collection(name)
        if COLL_LINKS not in existing:
            self._db.create_collection(COLL_LINKS, edge=True)

        self._db.collection(COLL_CHUNKS).add_index(
            {"type": "persistent", "fields": ["tenant_id", "project_id", "index"]}
        )
        self._db.collection(COLL_CHUNKS).add_index(
            {"type": "persistent", "fields": ["entity_id"]}
        )
        self._db.collection(COLL_CHUNKS).add_index(
            {"type": "persistent", "fields": ["source_id"]}
        )
        self._db.collection(COLL_SOURCES).add_index(
            {"type": "persistent", "fields": ["tenant_id", "project_id"]}
        )

        # ArangoSearch view for the BM25 lexical arm (R-400-202). Indexes
        # `content` with the built-in `text_en` analyzer ; identity/scope
        # fields are read from the doc at query time (post-SEARCH FILTER).
        # Low commit/consolidation intervals so a freshly-ingested chunk
        # becomes searchable quickly (prod default 1s is also fine).
        if VIEW_CHUNKS not in {v["name"] for v in self._db.views()}:
            self._db.create_arangosearch_view(
                VIEW_CHUNKS,
                properties={
                    "links": {
                        COLL_CHUNKS: {
                            "fields": {"content": {"analyzers": ["text_en"]}},
                        },
                    },
                    "commitIntervalMsec": 200,
                    "consolidationIntervalMsec": 500,
                },
            )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    def _upsert_chunk_sync(self, chunk: dict[str, Any]) -> None:
        self._db.collection(COLL_CHUNKS).insert(chunk, overwrite=True)

    async def upsert_chunk(self, chunk: dict[str, Any]) -> None:
        await self._run(self._upsert_chunk_sync, chunk)

    def _upsert_chunks_sync(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        self._db.collection(COLL_CHUNKS).insert_many(chunks, overwrite=True)

    async def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        await self._run(self._upsert_chunks_sync, chunks)

    def _scan_chunks_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        indexes: list[str],
        model_id: str,
        include_deprecated: bool,
        include_history: bool,
        scan_cap: int,
    ) -> list[dict[str, Any]]:
        # `active` always passes; `deprecated` / `superseded` are each
        # gated by their own flag so the retriever surfaces the correct
        # audit subset (R-400-031 / R-400-032).
        aql = """
        FOR c IN memory_chunks
            FILTER c.tenant_id == @tenant_id
                AND c.project_id == @project_id
                AND c.index IN @indexes
                AND c.model_id == @model_id
                AND (
                    c.status == 'active'
                    OR (c.status == 'deprecated' AND @include_deprecated)
                    OR (c.status == 'superseded' AND @include_history)
                )
            LIMIT @scan_cap
            RETURN c
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "tenant_id": tenant_id,
                "project_id": project_id,
                "indexes": indexes,
                "model_id": model_id,
                "include_deprecated": include_deprecated,
                "include_history": include_history,
                "scan_cap": scan_cap,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def scan_chunks(
        self,
        *,
        tenant_id: str,
        project_id: str,
        indexes: list[str],
        model_id: str,
        include_deprecated: bool,
        include_history: bool,
        scan_cap: int,
    ) -> list[dict[str, Any]]:
        """Return all chunks matching the filter, capped at `scan_cap`.

        The retriever applies cosine similarity client-side. Per
        R-400-011, the scan is deliberately bounded — callers are
        expected to narrow via metadata filters beyond that cap.
        """
        return await self._run(
            self._scan_chunks_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            indexes=indexes,
            model_id=model_id,
            include_deprecated=include_deprecated,
            include_history=include_history,
            scan_cap=scan_cap,
        )

    def _fetch_chunks_for_source_ids_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_ids: list[str],
        indexes: list[str],
        model_id: str,
        include_deprecated: bool,
        include_history: bool,
    ) -> list[dict[str, Any]]:
        if not source_ids:
            return []
        aql = """
        FOR c IN memory_chunks
            FILTER c.tenant_id == @tenant_id
                AND c.project_id == @project_id
                AND c.source_id IN @source_ids
                AND c.index IN @indexes
                AND c.model_id == @model_id
                AND (
                    c.status == 'active'
                    OR (c.status == 'deprecated' AND @include_deprecated)
                    OR (c.status == 'superseded' AND @include_history)
                )
            RETURN c
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "tenant_id": tenant_id,
                "project_id": project_id,
                "source_ids": source_ids,
                "indexes": indexes,
                "model_id": model_id,
                "include_deprecated": include_deprecated,
                "include_history": include_history,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def fetch_chunks_for_source_ids(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_ids: list[str],
        indexes: list[str],
        model_id: str,
        include_deprecated: bool,
        include_history: bool,
    ) -> list[dict[str, Any]]:
        """Targeted fetch of chunks belonging to an explicit list of
        source_ids (Phase F.2 — KG expansion path). Same tenant/project/
        index/model gating as `scan_chunks` but no scan_cap — the caller
        is expected to bound `source_ids` cardinality."""
        return await self._run(
            self._fetch_chunks_for_source_ids_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            source_ids=source_ids,
            indexes=indexes,
            model_id=model_id,
            include_deprecated=include_deprecated,
            include_history=include_history,
        )

    # ------------------------------------------------------------------
    # BM25 lexical arm of hybrid retrieval (R-400-202).
    # ------------------------------------------------------------------

    def _lexical_search_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        query: str,
        indexes: list[str],
        model_id: str,
        include_deprecated: bool,
        include_history: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        # BM25 over the ArangoSearch view, scoped to the same tenant /
        # project / index / model / status as the dense scan. Identity
        # fields are filtered post-SEARCH (read from the doc). Each row
        # carries its BM25 `score` so the service can rank-fuse (RRF).
        aql = """
        FOR c IN memory_chunks_search
            SEARCH ANALYZER(c.content IN TOKENS(@query, "text_en"), "text_en")
            FILTER c.tenant_id == @tenant_id
                AND c.project_id == @project_id
                AND c.index IN @indexes
                AND c.model_id == @model_id
                AND (
                    c.status == 'active'
                    OR (c.status == 'deprecated' AND @include_deprecated)
                    OR (c.status == 'superseded' AND @include_history)
                )
            SORT BM25(c) DESC
            LIMIT @limit
            RETURN MERGE(c, { score: BM25(c) })
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "query": query,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "indexes": indexes,
                "model_id": model_id,
                "include_deprecated": include_deprecated,
                "include_history": include_history,
                "limit": limit,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def lexical_search(
        self,
        *,
        tenant_id: str,
        project_id: str,
        query: str,
        indexes: list[str],
        model_id: str,
        include_deprecated: bool,
        include_history: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        """BM25 lexical retrieval (R-400-202). Returns up to `limit` chunk
        rows ordered by BM25 relevance, each with its `score`. Recovers
        exact-token matches (IDs, names, `R-NNN`) the dense arm can miss.
        Empty when the query has no `text_en` tokens or the view has not
        yet indexed freshly-ingested chunks (ArangoSearch commits async)."""
        return await self._run(
            self._lexical_search_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            query=query,
            indexes=indexes,
            model_id=model_id,
            include_deprecated=include_deprecated,
            include_history=include_history,
            limit=limit,
        )

    def _delete_chunks_for_source_sync(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> int:
        aql = """
        FOR c IN memory_chunks
            FILTER c.tenant_id == @tenant_id
                AND c.project_id == @project_id
                AND c.source_id == @source_id
            REMOVE c IN memory_chunks
            RETURN 1
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "tenant_id": tenant_id,
                "project_id": project_id,
                "source_id": source_id,
            },
        )
        return len(list(cursor))

    async def delete_chunks_for_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> int:
        return await self._run(
            self._delete_chunks_for_source_sync, tenant_id, project_id, source_id
        )

    def _mark_entity_superseded_sync(
        self,
        tenant_id: str,
        project_id: str,
        entity_id: str,
        new_version: int,
    ) -> int:
        aql = """
        FOR c IN memory_chunks
            FILTER c.tenant_id == @tenant_id
                AND c.project_id == @project_id
                AND c.entity_id == @entity_id
                AND c.entity_version < @new_version
                AND c.status == 'active'
            UPDATE c WITH { status: 'superseded' } IN memory_chunks
            RETURN 1
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "tenant_id": tenant_id,
                "project_id": project_id,
                "entity_id": entity_id,
                "new_version": new_version,
            },
        )
        return len(list(cursor))

    async def mark_entity_superseded(
        self,
        tenant_id: str,
        project_id: str,
        entity_id: str,
        new_version: int,
    ) -> int:
        return await self._run(
            self._mark_entity_superseded_sync,
            tenant_id,
            project_id,
            entity_id,
            new_version,
        )

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def _upsert_source_sync(self, source: dict[str, Any]) -> None:
        self._db.collection(COLL_SOURCES).insert(source, overwrite=True)

    async def upsert_source(self, source: dict[str, Any]) -> None:
        await self._run(self._upsert_source_sync, source)

    def _get_source_sync(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> dict[str, Any] | None:
        key = f"{tenant_id}:{project_id}:{source_id}"
        return cast(
            dict[str, Any] | None, self._db.collection(COLL_SOURCES).get(key)
        )

    async def get_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> dict[str, Any] | None:
        return await self._run(
            self._get_source_sync, tenant_id, project_id, source_id
        )

    def _list_sources_sync(
        self, tenant_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        aql = """
        FOR s IN memory_sources
            FILTER s.tenant_id == @tenant_id AND s.project_id == @project_id
            SORT s.uploaded_at DESC
            RETURN s
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"tenant_id": tenant_id, "project_id": project_id}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_sources(
        self, tenant_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        return await self._run(self._list_sources_sync, tenant_id, project_id)

    def _delete_source_sync(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> None:
        key = f"{tenant_id}:{project_id}:{source_id}"
        coll = self._db.collection(COLL_SOURCES)
        if coll.has(key):
            coll.delete(key)

    async def delete_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> None:
        await self._run(self._delete_source_sync, tenant_id, project_id, source_id)

    # ------------------------------------------------------------------
    # Quota (R-400-024)
    # ------------------------------------------------------------------

    def _quota_totals_sync(
        self, tenant_id: str, project_id: str
    ) -> dict[str, int]:
        aql_sources = """
        FOR s IN memory_sources
            FILTER s.tenant_id == @tenant_id AND s.project_id == @project_id
            COLLECT AGGREGATE total_bytes = SUM(s.size_bytes),
                              source_count = COUNT(s)
            RETURN { total_bytes, source_count }
        """
        aql_chunks = """
        FOR c IN memory_chunks
            FILTER c.tenant_id == @tenant_id AND c.project_id == @project_id
            COLLECT AGGREGATE chunk_count = COUNT(c)
            RETURN { chunk_count }
        """
        bind = {"tenant_id": tenant_id, "project_id": project_id}
        s_row = next(iter(self._db.aql.execute(aql_sources, bind_vars=bind)), None) or {}
        c_row = next(iter(self._db.aql.execute(aql_chunks, bind_vars=bind)), None) or {}
        return {
            "bytes_used": int(s_row.get("total_bytes") or 0),
            "source_count": int(s_row.get("source_count") or 0),
            "chunk_count": int(c_row.get("chunk_count") or 0),
        }

    async def quota_totals(
        self, tenant_id: str, project_id: str
    ) -> dict[str, int]:
        return await self._run(self._quota_totals_sync, tenant_id, project_id)


def make_repository(
    host: str, port: int, username: str, password: str, db_name: str
) -> MemoryRepository:
    """Factory used by the FastAPI lifespan and integration tests."""
    client = ArangoClient(hosts=f"http://{host}:{port}")
    db = client.db(db_name, username=username, password=password)
    return MemoryRepository(db)
