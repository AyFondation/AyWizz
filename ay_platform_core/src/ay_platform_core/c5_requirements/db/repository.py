# =============================================================================
# File: repository.py
# Version: 4
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/db/repository.py
# Description: ArangoDB derived-index for C5. Collections per R-300-012:
#              req_entities, req_documents, req_relations (edge),
#              req_history, req_idempotency (TTL), req_reindex_jobs.
#              All public methods are async wrappers around python-arango
#              (sync) via asyncio.to_thread, matching the C2/C3 pattern.
#              v2: index creation via add_index({'type': ...}) — the
#                  per-type helpers (add_persistent_index, add_ttl_index)
#                  are deprecated in python-arango 8.x and raise warnings
#                  that pytest promotes to errors (filterwarnings=error).
#
# @relation implements:R-300-012
# @relation implements:R-300-013
# @relation implements:R-300-021
# @relation implements:R-300-040
# @relation implements:R-300-070
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from arango import ArangoClient  # type: ignore[attr-defined]

_T = TypeVar("_T")

COLL_ENTITIES = "req_entities"
COLL_DOCUMENTS = "req_documents"
COLL_RELATIONS = "req_relations"
COLL_HISTORY = "req_history"
COLL_IDEMPOTENCY = "req_idempotency"
COLL_REINDEX_JOBS = "req_reindex_jobs"


class RequirementsRepository:
    """Sync ArangoDB operations wrapped for async use via asyncio.to_thread.

    python-arango's `Database` object is NOT thread-safe: concurrent calls
    from the default ThreadPoolExecutor deadlock or corrupt internal state.
    We serialise every wrapped call via `self._lock` so that at most one
    thread touches `self._db` at a time. The lock is created lazily on
    first use to stay compatible with repository instantiation outside an
    event loop (tests, CLI).
    """

    def __init__(self, db: Any) -> None:
        self._db = db
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run(
        self, func: Callable[..., _T], *args: Any, **kwargs: Any
    ) -> _T:
        """Run a sync db operation in a thread with exclusive access."""
        async with self._get_lock():
            return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------
    # Collection bootstrap (idempotent)
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        doc_collections = (
            COLL_ENTITIES,
            COLL_DOCUMENTS,
            COLL_HISTORY,
            COLL_IDEMPOTENCY,
            COLL_REINDEX_JOBS,
        )
        for name in doc_collections:
            if name not in existing:
                self._db.create_collection(name)
        if COLL_RELATIONS not in existing:
            self._db.create_collection(COLL_RELATIONS, edge=True)

        # Persistent indexes that support the hot paths identified in §5.1
        self._db.collection(COLL_ENTITIES).add_index(
            {"type": "persistent", "fields": ["project_id", "status"]}
        )
        self._db.collection(COLL_ENTITIES).add_index(
            {"type": "persistent", "fields": ["project_id", "category"]}
        )
        self._db.collection(COLL_HISTORY).add_index(
            {"type": "persistent", "fields": ["project_id", "entity_id"]}
        )
        # Idempotency cache expiry via TTL index on `expires_at` (R-300-021)
        self._db.collection(COLL_IDEMPOTENCY).add_index(
            {"type": "ttl", "fields": ["expires_at"], "expireAfter": 0}
        )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def _upsert_document_sync(self, doc: dict[str, Any]) -> None:
        # overwrite=True makes insert act as upsert regardless of _rev;
        # avoids HTTP 412 conflicts when a stale _rev lingers on the row
        # (common when the same row is re-read between write bursts).
        self._db.collection(COLL_DOCUMENTS).insert(doc, overwrite=True)

    async def upsert_document(self, doc: dict[str, Any]) -> None:
        await self._run(self._upsert_document_sync, doc)

    def _get_document_sync(
        self, project_id: str, slug: str
    ) -> dict[str, Any] | None:
        key = f"{project_id}:{slug}"
        return cast(dict[str, Any] | None, self._db.collection(COLL_DOCUMENTS).get(key))

    async def get_document(
        self, project_id: str, slug: str
    ) -> dict[str, Any] | None:
        return await self._run(self._get_document_sync, project_id, slug)

    def _list_documents_sync(
        self, project_id: str, limit: int, cursor_key: str | None
    ) -> list[dict[str, Any]]:
        aql = """
        FOR d IN req_documents
            FILTER d.project_id == @pid
                AND (@cursor == null OR d._key > @cursor)
            SORT d._key ASC
            LIMIT @limit
            RETURN d
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={"pid": project_id, "cursor": cursor_key, "limit": limit},
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_documents(
        self, project_id: str, *, limit: int = 50, cursor_key: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._run(
            self._list_documents_sync, project_id, limit, cursor_key
        )

    def _delete_document_sync(self, project_id: str, slug: str) -> None:
        key = f"{project_id}:{slug}"
        coll = self._db.collection(COLL_DOCUMENTS)
        if coll.has(key):
            coll.delete(key)

    async def delete_document(self, project_id: str, slug: str) -> None:
        await self._run(self._delete_document_sync, project_id, slug)

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def _upsert_entity_sync(self, entity: dict[str, Any]) -> None:
        self._db.collection(COLL_ENTITIES).insert(entity, overwrite=True)

    async def upsert_entity(self, entity: dict[str, Any]) -> None:
        await self._run(self._upsert_entity_sync, entity)

    def _get_entity_sync(
        self, project_id: str, entity_id: str
    ) -> dict[str, Any] | None:
        key = f"{project_id}:{entity_id}"
        return cast(dict[str, Any] | None, self._db.collection(COLL_ENTITIES).get(key))

    async def get_entity(
        self, project_id: str, entity_id: str
    ) -> dict[str, Any] | None:
        return await self._run(self._get_entity_sync, project_id, entity_id)

    def _list_entities_sync(
        self,
        project_id: str,
        *,
        limit: int,
        cursor_key: str | None,
        status: str | None,
        category: str | None,
        domain: str | None,
        text: str | None,
    ) -> list[dict[str, Any]]:
        aql = """
        FOR e IN req_entities
            FILTER e.project_id == @pid
                AND (@cursor == null OR e._key > @cursor)
                AND (@status == null OR e.status == @status)
                AND (@category == null OR e.category == @category)
                AND (@domain == null OR e.domain == @domain)
                AND (@text == null
                     OR LIKE(LOWER(e.entity_id), CONCAT('%', LOWER(@text), '%'), true)
                     OR LIKE(LOWER(e.title), CONCAT('%', LOWER(@text), '%'), true))
            SORT e._key ASC
            LIMIT @limit
            RETURN e
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "pid": project_id,
                "cursor": cursor_key,
                "status": status,
                "category": category,
                "domain": domain,
                "text": text,
                "limit": limit,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_entities(
        self,
        project_id: str,
        *,
        limit: int = 50,
        cursor_key: str | None = None,
        status: str | None = None,
        category: str | None = None,
        domain: str | None = None,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._run(
            self._list_entities_sync,
            project_id,
            limit=limit,
            cursor_key=cursor_key,
            status=status,
            category=category,
            domain=domain,
            text=text,
        )

    def _delete_entity_sync(self, project_id: str, entity_id: str) -> None:
        key = f"{project_id}:{entity_id}"
        coll = self._db.collection(COLL_ENTITIES)
        if coll.has(key):
            coll.delete(key)

    async def delete_entity(self, project_id: str, entity_id: str) -> None:
        await self._run(self._delete_entity_sync, project_id, entity_id)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _append_history_sync(self, snapshot: dict[str, Any]) -> None:
        self._db.collection(COLL_HISTORY).insert(snapshot)

    async def append_history(self, snapshot: dict[str, Any]) -> None:
        await self._run(self._append_history_sync, snapshot)

    def _list_history_sync(
        self, project_id: str, entity_id: str
    ) -> list[dict[str, Any]]:
        aql = """
        FOR h IN req_history
            FILTER h.project_id == @pid AND h.entity_id == @eid
            SORT h.version ASC
            RETURN h
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "eid": entity_id}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_history(
        self, project_id: str, entity_id: str
    ) -> list[dict[str, Any]]:
        return await self._run(self._list_history_sync, project_id, entity_id)

    # ------------------------------------------------------------------
    # Relations (edge collection)
    # ------------------------------------------------------------------

    def _replace_entity_relations_sync(
        self, entity_key: str, edges: list[dict[str, Any]]
    ) -> None:
        # Delete existing outbound edges from this entity, then insert the new set.
        aql = """
        FOR e IN req_relations
            FILTER e._from == @from
            REMOVE e IN req_relations
        """
        self._db.aql.execute(
            aql, bind_vars={"from": f"{COLL_ENTITIES}/{entity_key}"}
        )
        if edges:
            self._db.collection(COLL_RELATIONS).insert_many(edges)

    async def replace_entity_relations(
        self, entity_key: str, edges: list[dict[str, Any]]
    ) -> None:
        await self._run(
            self._replace_entity_relations_sync, entity_key, edges
        )

    def _list_relations_sync(
        self, project_id: str, source_id: str, rel_type: str | None
    ) -> list[dict[str, Any]]:
        aql = """
        FOR e IN req_relations
            FILTER e._from == @from
                AND (@type == null OR e.type == @type)
            RETURN e
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "from": f"{COLL_ENTITIES}/{project_id}:{source_id}",
                "type": rel_type,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_relations(
        self, project_id: str, source_id: str, rel_type: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._run(
            self._list_relations_sync, project_id, source_id, rel_type
        )

    # ------------------------------------------------------------------
    # Idempotency cache (R-300-021)
    # ------------------------------------------------------------------

    def _get_idempotency_sync(self, key: str) -> dict[str, Any] | None:
        return cast(
            dict[str, Any] | None, self._db.collection(COLL_IDEMPOTENCY).get(key)
        )

    async def get_idempotency(self, key: str) -> dict[str, Any] | None:
        return await self._run(self._get_idempotency_sync, key)

    def _put_idempotency_sync(
        self, key: str, response_body: str, status_code: int, ttl_seconds: int
    ) -> None:
        now = datetime.now(UTC)
        self._db.collection(COLL_IDEMPOTENCY).insert(
            {
                "_key": key,
                "response_body": response_body,
                "status_code": status_code,
                "created_at": now.isoformat(),
                "expires_at": int(now.timestamp()) + ttl_seconds,
            },
            overwrite=True,
        )

    async def put_idempotency(
        self, key: str, response_body: str, status_code: int, ttl_seconds: int
    ) -> None:
        await self._run(
            self._put_idempotency_sync, key, response_body, status_code, ttl_seconds
        )

    # ------------------------------------------------------------------
    # Reindex jobs (R-300-070)
    # ------------------------------------------------------------------

    def _upsert_reindex_job_sync(self, job: dict[str, Any]) -> None:
        self._db.collection(COLL_REINDEX_JOBS).insert(job, overwrite=True)

    async def upsert_reindex_job(self, job: dict[str, Any]) -> None:
        await self._run(self._upsert_reindex_job_sync, job)

    def _get_reindex_job_sync(self, job_id: str) -> dict[str, Any] | None:
        return cast(
            dict[str, Any] | None, self._db.collection(COLL_REINDEX_JOBS).get(job_id)
        )

    async def get_reindex_job(self, job_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_reindex_job_sync, job_id)

    def _list_reindex_jobs_running_sync(self, project_id: str) -> list[dict[str, Any]]:
        aql = """
        FOR j IN req_reindex_jobs
            FILTER j.project_id == @pid
                AND (j.status == 'pending' OR j.status == 'running')
            SORT j.submitted_at DESC
            RETURN j
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def list_reindex_jobs_running(self, project_id: str) -> list[dict[str, Any]]:
        """R-300-072 idempotency support: find an already-in-flight reindex."""
        return await self._run(
            self._list_reindex_jobs_running_sync, project_id
        )


def make_repository(
    host: str, port: int, username: str, password: str, db_name: str
) -> RequirementsRepository:
    """Factory used by the FastAPI lifespan and integration tests."""
    client = ArangoClient(hosts=f"http://{host}:{port}")
    db = client.db(db_name, username=username, password=password)
    return RequirementsRepository(db)
