# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/storage/repository.py
# Description: ArangoDB repository for the storage time-series
#              (`storage_snapshots`, one doc per (project, measured_at)).
#              Sync-wrapped, lock-guarded — same pattern as the other c8
#              repositories. A snapshot is (tenant_id, project_id, measured_at,
#              bytes); the metering pass appends, the dashboards read a window.
# @relation implements:R-100-140
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast

COLL_SNAPSHOTS = "storage_snapshots"

_T = TypeVar("_T")


class StorageStore(Protocol):
    """Storage seam the StorageService depends on (Arango concrete, fake in
    tests)."""

    async def insert_snapshot(self, document: dict[str, Any]) -> None: ...
    async def series_since(
        self, project_id: str, since_iso: str
    ) -> list[tuple[str, int]]: ...


class StorageSnapshotRepository:
    """Sync ArangoDB operations for `storage_snapshots`, wrapped for async."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run(self, func: Callable[..., _T], *args: Any) -> _T:
        async with self._get_lock():
            return await asyncio.to_thread(func, *args)

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_SNAPSHOTS not in existing:
            self._db.create_collection(COLL_SNAPSHOTS)
        self._db.collection(COLL_SNAPSHOTS).add_index(
            {"type": "persistent", "fields": ["project_id", "measured_at"]}
        )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    def _insert_snapshot_sync(self, document: dict[str, Any]) -> None:
        self._db.collection(COLL_SNAPSHOTS).insert(document)

    async def insert_snapshot(self, document: dict[str, Any]) -> None:
        await self._run(self._insert_snapshot_sync, document)

    def _series_since_sync(
        self, project_id: str, since_iso: str
    ) -> list[tuple[str, int]]:
        if not self._db.has_collection(COLL_SNAPSHOTS):
            return []
        cursor = self._db.aql.execute(
            "FOR s IN @@col "
            "  FILTER s.project_id == @pid AND s.measured_at >= @since "
            "  SORT s.measured_at ASC "
            "  RETURN { measured_at: s.measured_at, bytes: s.bytes }",
            bind_vars={"@col": COLL_SNAPSHOTS, "pid": project_id, "since": since_iso},
        )
        return [
            (str(r["measured_at"]), int(r.get("bytes") or 0))
            for r in cast("list[dict[str, Any]]", list(cursor))
        ]

    async def series_since(
        self, project_id: str, since_iso: str
    ) -> list[tuple[str, int]]:
        """The (measured_at, bytes) points for a project since `since_iso`,
        oldest first."""
        return await self._run(self._series_since_sync, project_id, since_iso)
