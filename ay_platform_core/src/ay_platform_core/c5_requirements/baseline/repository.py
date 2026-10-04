# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/baseline/repository.py
# Description: The derived index of baselines — E-310-006's `req_baselines`.
#
#              METADATA ONLY, as everywhere else in C5. The row carries the
#              counts and the provenance so "what baselines does this
#              project have?" is one query, and the manifest itself stays
#              in MinIO (`R-310-002`).
#
#              THERE IS A `drop_baseline` AND IT IS FOR REINDEX ONLY. Unlike
#              the manifest — which has no delete at any layer (`R-310-204`)
#              — an INDEX row is derived state and must be removable for a
#              rebuild. The docstring says so, because the next reader will
#              otherwise read this method as a way to delete a baseline.
#
# @relation implements:R-310-200
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .models import BaselineManifest

_T = TypeVar("_T")

COLL_BASELINES = "req_baselines"


def to_baseline_row(manifest: BaselineManifest) -> dict[str, Any]:
    """Project a manifest onto its index row."""
    return {
        "_key": f"{manifest.project_id}:{manifest.tag}",
        "tag": manifest.tag,
        "project_id": manifest.project_id,
        "cycle_id": manifest.cycle_id,
        "cycle_version": manifest.cycle_version,
        "created_by": manifest.created_by,
        "created_at": manifest.created_at.isoformat(),
        "object_count": manifest.object_count,
        "link_count": manifest.link_count,
        "containers": list(manifest.containers),
        "note": manifest.note,
    }


class BaselineRepository:
    """The queryable index of baselines.

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
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {collection["name"] for collection in self._db.collections()}
        if COLL_BASELINES not in existing:
            self._db.create_collection(COLL_BASELINES)

    async def ensure_collections(self) -> None:
        """Create the baseline index if it is absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _insert_sync(self, row: dict[str, Any]) -> None:
        self._db.collection(COLL_BASELINES).insert(row, overwrite=True)

    async def put_baseline(self, manifest: BaselineManifest) -> None:
        """Index one baseline.

        `overwrite=True` here is safe and NOT a contradiction of
        immutability: the manifest write refuses an existing tag before
        this is reached, so the only writer is a reindex re-deriving the
        same row from the same manifest.
        """
        await self._run(self._insert_sync, to_baseline_row(manifest))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _list_sync(self, project_id: str) -> list[dict[str, Any]]:
        aql = """
        FOR b IN req_baselines
            FILTER b.project_id == @pid
            SORT b.created_at DESC, b._key ASC
            RETURN b
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def list_baselines(self, project_id: str) -> list[dict[str, Any]]:
        """Return a project's baselines, newest first."""
        return await self._run(self._list_sync, project_id)

    def _referenced_sync(self, project_id: str) -> list[dict[str, Any]]:
        aql = """
        FOR b IN req_baselines
            FILTER b.project_id == @pid
            RETURN { tag: b.tag, object_count: b.object_count }
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def baseline_count(self, project_id: str) -> int:
        """Return how many baselines a project holds."""
        return len(await self._run(self._referenced_sync, project_id))

    def _drop_sync(self, key: str) -> bool:
        collection = self._db.collection(COLL_BASELINES)
        if not collection.has(key):
            return False
        collection.delete(key)
        return True

    async def drop_baseline(self, project_id: str, tag: str) -> bool:
        """Remove one INDEX row — for a reindex rebuild only.

        This does NOT delete the baseline: the manifest has no delete at
        any layer, because the object versions it names are retained
        precisely because it names them (`R-310-204`). Removing the row
        and not re-deriving it would hide a baseline that still exists.
        """
        return await self._run(self._drop_sync, f"{project_id}:{tag}")
