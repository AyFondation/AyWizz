# =============================================================================
# File: test_storage_repository.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_storage_repository.py
# Description: Unit tests for the ArangoDB-backed `StorageSnapshotRepository`
#              AQL layer (R-100-140 storage metering time-series): the
#              collection bootstrap (create + persistent index), snapshot
#              insert, and the ordered `series_since` projection. A fake `db`
#              records calls so we assert the query filters the project + sorts
#              ascending and maps rows to `(measured_at, bytes)`. The router /
#              meter layers are covered separately over fakes; this file covers
#              the query bodies those fakes bypass.
#
# @relation validates:R-100-140
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c8_llm.storage.repository import (
    COLL_SNAPSHOTS,
    StorageSnapshotRepository,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.indexes: list[dict[str, Any]] = []

    def insert(self, document: dict[str, Any]) -> None:
        self.inserted.append(document)

    def add_index(self, spec: dict[str, Any]) -> None:
        self.indexes.append(spec)


class _FakeAql:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_query: str | None = None
        self.last_bind: dict[str, Any] = {}

    def execute(self, query: str, bind_vars: dict[str, Any] | None = None) -> Any:
        self.last_query = query
        self.last_bind = dict(bind_vars or {})
        return iter(self._rows)


class _FakeDb:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        has: bool = True,
        existing: list[str] | None = None,
    ) -> None:
        self.aql = _FakeAql(rows or [])
        self._has = has
        self._existing = existing if existing is not None else [COLL_SNAPSHOTS]
        self.coll = _FakeCollection()
        self.created: list[str] = []

    def has_collection(self, name: str) -> bool:
        return self._has

    def collections(self) -> list[dict[str, str]]:
        return [{"name": n} for n in self._existing]

    def create_collection(self, name: str) -> None:
        self.created.append(name)

    def collection(self, name: str) -> _FakeCollection:
        return self.coll


# --- series_since ----------------------------------------------------------


async def test_series_since_maps_points_in_order() -> None:
    db = _FakeDb(
        [
            {"measured_at": "2026-01-01T00:00:00Z", "bytes": 100},
            {"measured_at": "2026-01-02T00:00:00Z", "bytes": 250},
        ]
    )
    out = await StorageSnapshotRepository(db).series_since("p1", "2026-01-01T00:00:00Z")
    assert out == [("2026-01-01T00:00:00Z", 100), ("2026-01-02T00:00:00Z", 250)]
    assert db.aql.last_bind["pid"] == "p1"
    assert "SORT s.measured_at ASC" in (db.aql.last_query or "")


async def test_series_since_missing_collection_is_empty() -> None:
    db = _FakeDb(has=False)
    assert await StorageSnapshotRepository(db).series_since("p1", "2026-01-01T00:00:00Z") == []


async def test_series_since_coerces_missing_bytes_to_zero() -> None:
    db = _FakeDb([{"measured_at": "2026-01-01T00:00:00Z"}])
    out = await StorageSnapshotRepository(db).series_since("p1", "2026-01-01T00:00:00Z")
    assert out == [("2026-01-01T00:00:00Z", 0)]


# --- insert_snapshot -------------------------------------------------------


async def test_insert_snapshot_writes_document() -> None:
    db = _FakeDb()
    doc = {"project_id": "p1", "measured_at": "2026-01-01T00:00:00Z", "bytes": 42}
    await StorageSnapshotRepository(db).insert_snapshot(doc)
    assert db.coll.inserted == [doc]


# --- ensure_collections ----------------------------------------------------


async def test_ensure_collections_creates_when_absent_and_indexes() -> None:
    db = _FakeDb(existing=[])  # snapshots collection not yet present
    await StorageSnapshotRepository(db).ensure_collections()
    assert db.created == [COLL_SNAPSHOTS]
    assert db.coll.indexes == [
        {"type": "persistent", "fields": ["project_id", "measured_at"]}
    ]


async def test_ensure_collections_skips_create_when_present() -> None:
    db = _FakeDb(existing=[COLL_SNAPSHOTS])
    await StorageSnapshotRepository(db).ensure_collections()
    assert db.created == []
    # The index is still asserted (idempotent) even when the collection exists.
    assert len(db.coll.indexes) == 1
