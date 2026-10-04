# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/repository.py
# Description: ArangoDB derived index for the object-grain document model
#              (310-SPEC-DOC-TRACEABILITY §4.1, E-310-006).
#
#              This index holds NOTHING that is not derivable from MinIO
#              (R-310-002). It exists for the read paths MinIO serves badly:
#              ordered listing of a container, and filtering by review state
#              for the review queue. `rebuild_container` restores it from the
#              source of truth, so a total loss of ArangoDB is a service
#              outage, not data loss.
#
#              Object bodies are deliberately NOT indexed: duplicating them
#              would create a second place where content can drift from the
#              source of truth. The index carries metadata and a content hash
#              only.
#
#              Follows the C5 repository pattern: sync python-arango calls
#              wrapped for async use and serialised by an asyncio.Lock,
#              because python-arango's Database is not thread-safe.
#
# @relation implements:R-310-002
# =============================================================================

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .models import DocObject

_T = TypeVar("_T")

COLL_OBJECTS = "req_objects"


def index_key(project_id: str, object_id: str) -> str:
    """Return the ArangoDB `_key` of an object's index row.

    Per E-310-006 the key is `<project-id>:<object-id>`, which makes object
    identifiers unique within a project rather than within a container —
    consistent with R-310-005, under which an identifier is never reused.

    Args:
        project_id: Owning project.
        object_id: Object identifier.

    Returns:
        The composite document key.
    """
    return f"{project_id}:{object_id}"


def content_hash(obj: DocObject) -> str:
    """Return a stable digest of an object's authoritative content.

    Covers the body or the notation, whichever carries the content for this
    object type (R-310-008), so that a divergence between the index and MinIO
    is detectable without storing the content twice.

    Args:
        obj: The object to digest.

    Returns:
        A `sha256:<hex>` digest string.
    """
    payload = obj.notation if obj.notation is not None else (obj.body or "")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def to_index_row(obj: DocObject) -> dict[str, Any]:
    """Project a stored object onto its index row.

    The row carries metadata only — never the body — so the index cannot
    become a second source of truth for content (R-310-002).

    Args:
        obj: The object as persisted in MinIO.

    Returns:
        The ArangoDB document to upsert.
    """
    return {
        "_key": index_key(obj.project_id, obj.object_id),
        "project_id": obj.project_id,
        "object_id": obj.object_id,
        "container": obj.container,
        "type": obj.type.value,
        "parent": obj.parent,
        "ordinal": obj.ordinal,
        "version": obj.version,
        "review_state": obj.review_state.value,
        "content_hash": content_hash(obj),
        "produced_by_workflow": (
            obj.produced_by.workflow if obj.produced_by is not None else None
        ),
        "produced_by_version": (
            obj.produced_by.workflow_version if obj.produced_by is not None else None
        ),
        "cycle_version": obj.cycle_version,
        "last_decision": (
            obj.last_review.decision.value if obj.last_review is not None else None
        ),
        "last_decision_actor": (
            obj.last_review.actor if obj.last_review is not None else None
        ),
        "created_at": obj.created_at.isoformat(),
        "created_by": obj.created_by,
        "updated_at": obj.updated_at.isoformat(),
        "updated_by": obj.updated_by,
    }


class ObjectRepository:
    """Derived-index operations for document objects.

    Args:
        db: A python-arango `Database` handle.
    """

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
    # Bootstrap (idempotent)
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_OBJECTS not in existing:
            self._db.create_collection(COLL_OBJECTS)
        collection = self._db.collection(COLL_OBJECTS)
        # Ordered listing of a container is the hot read path (the document
        # view renders objects in order).
        collection.add_index(
            {"type": "persistent", "fields": ["project_id", "container", "ordinal"]}
        )
        # The review queue filters by state across a whole project.
        collection.add_index(
            {"type": "persistent", "fields": ["project_id", "review_state"]}
        )

    async def ensure_collections(self) -> None:
        """Create the object collection and its indexes if absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _upsert_sync(self, row: dict[str, Any]) -> None:
        # overwrite=True makes insert act as upsert regardless of _rev, which
        # avoids HTTP 412 when a stale _rev lingers between write bursts.
        self._db.collection(COLL_OBJECTS).insert(row, overwrite=True)

    async def upsert(self, obj: DocObject) -> None:
        """Index (or re-index) one object."""
        await self._run(self._upsert_sync, to_index_row(obj))

    def _delete_sync(self, key: str) -> bool:
        collection = self._db.collection(COLL_OBJECTS)
        if not collection.has(key):
            return False
        collection.delete(key)
        return True

    async def delete(self, project_id: str, object_id: str) -> bool:
        """Drop one object's index row.

        Returns:
            True when a row was removed, False when there was none.
        """
        return await self._run(self._delete_sync, index_key(project_id, object_id))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _get_sync(self, key: str) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, self._db.collection(COLL_OBJECTS).get(key))

    async def get(self, project_id: str, object_id: str) -> dict[str, Any] | None:
        """Return one object's index row, or None."""
        return await self._run(self._get_sync, index_key(project_id, object_id))

    def _list_container_sync(
        self, project_id: str, container: str
    ) -> list[dict[str, Any]]:
        aql = """
        FOR o IN req_objects
            FILTER o.project_id == @pid AND o.container == @container
            SORT o.ordinal ASC, o.object_id ASC
            RETURN o
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "container": container}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_container(
        self, project_id: str, container: str
    ) -> list[dict[str, Any]]:
        """Return a container's index rows ordered by position.

        `object_id` breaks ties so the order is total: two objects sharing an
        ordinal would otherwise render non-deterministically.
        """
        return await self._run(self._list_container_sync, project_id, container)

    def _list_by_state_sync(
        self, project_id: str, review_state: str
    ) -> list[dict[str, Any]]:
        aql = """
        FOR o IN req_objects
            FILTER o.project_id == @pid AND o.review_state == @state
            SORT o.container ASC, o.ordinal ASC
            RETURN o
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "state": review_state}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_by_review_state(
        self, project_id: str, review_state: str
    ) -> list[dict[str, Any]]:
        """Return every object of a project in the given review state."""
        return await self._run(self._list_by_state_sync, project_id, review_state)

    def _count_by_state_sync(self, project_id: str) -> dict[str, int]:
        aql = """
        FOR o IN req_objects
            FILTER o.project_id == @pid
            COLLECT state = o.review_state WITH COUNT INTO n
            RETURN { state: state, n: n }
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return {row["state"]: row["n"] for row in cursor}

    async def count_by_review_state(self, project_id: str) -> dict[str, int]:
        """Return object counts per review state for a project.

        Feeds the coverage figures of R-310-225, which must be able to state
        the `auto-accepted` share separately from `accepted`.
        """
        return await self._run(self._count_by_state_sync, project_id)

    # ------------------------------------------------------------------
    # Rebuild — R-310-002
    # ------------------------------------------------------------------

    def _drop_container_sync(self, project_id: str, container: str) -> int:
        aql = """
        FOR o IN req_objects
            FILTER o.project_id == @pid AND o.container == @container
            REMOVE o IN req_objects
            COLLECT WITH COUNT INTO n
            RETURN n
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "container": container}
        )
        removed = list(cursor)
        return int(removed[0]) if removed else 0

    async def rebuild_container(
        self, project_id: str, container: str, objects: list[DocObject]
    ) -> int:
        """Replace a container's index rows with the given objects.

        The drop and the re-index are NOT one transaction: the index is a
        cache (R-310-002), so a crash between the two leaves it incomplete,
        never wrong, and re-running restores it. Making this atomic would buy
        nothing the source of truth does not already guarantee.

        Args:
            project_id: Owning project.
            container: Container slug.
            objects: The objects read back from MinIO.

        Returns:
            The number of rows written.
        """
        await self._run(self._drop_container_sync, project_id, container)
        for obj in objects:
            await self.upsert(obj)
        return len(objects)
