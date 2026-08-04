# =============================================================================
# File: test_storage_metering.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_storage_metering.py
# Description: Unit tests for the per-project storage metering + service
#              (E-100-002 v7). A fake object store models MinIO's list_objects
#              (pseudo-dirs + sized blobs); an in-memory snapshot store models
#              the time-series. Validates project discovery from the key layout,
#              per-project byte sums, tenant-scoped reports, the snapshot pass,
#              and the series-since read.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from ay_platform_core.c8_llm.storage.metering import StorageMeter
from ay_platform_core.c8_llm.storage.service import StorageService
from ay_platform_core.c8_llm.storage.snapshot_job import run_once

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)


@dataclass
class _Obj:
    object_name: str
    size: int = 0
    is_dir: bool = False


class _FakeMinio:
    """Models minio.Minio.list_objects over a flat set of blob keys."""

    def __init__(self, blobs: dict[str, int]) -> None:
        # key → size, e.g. "c4-artifacts/t1/p1/runs/x/a.py": 100
        self._blobs = blobs

    def list_objects(
        self, bucket_name: str, prefix: str = "", recursive: bool = False
    ) -> list[_Obj]:
        if recursive:
            return [
                _Obj(k, v) for k, v in self._blobs.items() if k.startswith(prefix)
            ]
        # Non-recursive: immediate children (blobs + pseudo-dirs) of `prefix`.
        seen_dirs: set[str] = set()
        out: list[_Obj] = []
        for k in self._blobs:
            if not k.startswith(prefix):
                continue
            rest = k[len(prefix):]
            if "/" in rest:
                seen_dirs.add(prefix + rest.split("/", 1)[0] + "/")
            else:
                out.append(_Obj(k, self._blobs[k]))
        out.extend(_Obj(d, 0, is_dir=False) for d in sorted(seen_dirs))
        return out


class _FakeStore:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    async def insert_snapshot(self, document: dict[str, Any]) -> None:
        self.snapshots.append(dict(document))

    async def series_since(self, project_id: str, since_iso: str) -> list[tuple[str, int]]:
        return [
            (s["measured_at"], s["bytes"])
            for s in self.snapshots
            if s["project_id"] == project_id and s["measured_at"] >= since_iso
        ]


_BLOBS = {
    "c4-artifacts/t1/p1/runs/r1/a.py": 100,
    "c4-artifacts/t1/p1/runs/r1/b.py": 250,
    "c4-artifacts/t1/p2/runs/r9/c.md": 40,
    "c4-artifacts/t2/px/runs/r0/d.txt": 7,
}


def _meter() -> StorageMeter:
    return StorageMeter(_FakeMinio(_BLOBS), bucket="orchestrator")


async def test_discover_projects_from_key_layout() -> None:
    pairs = await _meter().discover_projects()
    assert set(pairs) == {("t1", "p1"), ("t1", "p2"), ("t2", "px")}


async def test_measure_project_sums_prefix_bytes() -> None:
    m = _meter()
    assert await m.measure_project("t1", "p1") == 350
    assert await m.measure_project("t1", "p2") == 40
    assert await m.measure_project("t2", "px") == 7


async def test_report_scoped_to_tenant() -> None:
    svc = StorageService(_meter(), _FakeStore(), clock=lambda: _NOW)
    report = await svc.project_storage_report(tenant_id="t1")
    assert report.tenant_id == "t1"
    assert {(p.project_id, p.bytes) for p in report.projects} == {("p1", 350), ("p2", 40)}
    # t2's project is excluded by the tenant scope.
    assert all(p.tenant_id == "t1" for p in report.projects)


async def test_report_platform_wide() -> None:
    svc = StorageService(_meter(), _FakeStore(), clock=lambda: _NOW)
    report = await svc.project_storage_report(tenant_id=None)
    assert {p.project_id for p in report.projects} == {"p1", "p2", "px"}


async def test_snapshot_pass_writes_one_per_project() -> None:
    store = _FakeStore()
    svc = StorageService(_meter(), store, clock=lambda: _NOW)
    result = await svc.run_snapshot()
    assert result.snapshots_written == 3
    assert {(s["project_id"], s["bytes"]) for s in store.snapshots} == {
        ("p1", 350),
        ("p2", 40),
        ("px", 7),
    }
    assert all(s["measured_at"] == _NOW.isoformat() for s in store.snapshots)


async def test_series_reads_snapshots_and_current() -> None:
    store = _FakeStore()
    svc = StorageService(_meter(), store, clock=lambda: _NOW)
    await svc.run_snapshot()  # one point for p1 at _NOW
    series = await svc.project_series("t1", "p1", "year")
    assert series.project_id == "p1"
    assert series.current_bytes == 350
    assert [pt.bytes for pt in series.points] == [350]


async def test_tenant_storage_report_sums_projects_per_tenant() -> None:
    svc = StorageService(_meter(), _FakeStore(), clock=lambda: _NOW)
    report = await svc.tenant_storage_report()
    totals = {t.tenant_id: t.bytes for t in report.tenants}
    # t1 = p1 (350) + p2 (40) = 390 ; t2 = px (7).
    assert totals == {"t1": 390, "t2": 7}


async def test_snapshot_job_run_once_delegates() -> None:
    store = _FakeStore()
    svc = StorageService(_meter(), store, clock=lambda: _NOW)
    result = await run_once(svc)
    assert result.snapshots_written == 3
    assert len(store.snapshots) == 3
