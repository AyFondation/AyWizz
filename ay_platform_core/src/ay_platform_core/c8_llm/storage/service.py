# =============================================================================
# File: service.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/storage/service.py
# Description: Application service for the per-project storage dashboards
#              (E-100-002 v7). Combines the live MinIO measure (current
#              occupation) with the snapshot time-series (history). The metering
#              pass (`run_snapshot`) is triggered by a periodic CronJob and
#              appends one snapshot per project.
# @relation implements:R-100-140
# =============================================================================

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ay_platform_core.c8_llm.storage.metering import StorageMeter
from ay_platform_core.c8_llm.storage.models import (
    ProjectStorage,
    ProjectStorageReport,
    ProjectStorageSeries,
    StorageSeriesPoint,
    StorageSnapshotResult,
    TenantStorage,
    TenantStorageReport,
)
from ay_platform_core.c8_llm.storage.repository import StorageStore


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _window_since(window: str, now: datetime) -> datetime:
    """Start instant of a storage series window. Unknown → last 24h."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    q = ((now.month - 1) // 3) * 3 + 1
    sem = 1 if now.month <= 6 else 7
    starts = {
        "day": day,
        "week": day - timedelta(days=day.weekday()),
        "month": _month_start(now),
        "quarter": now.replace(
            month=q, day=1, hour=0, minute=0, second=0, microsecond=0
        ),
        "semester": now.replace(
            month=sem, day=1, hour=0, minute=0, second=0, microsecond=0
        ),
        "year": now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
    }
    return starts.get(window, now - timedelta(days=1))


class StorageService:
    """Per-project storage reporting over a MinIO meter + snapshot store."""

    def __init__(
        self,
        meter: StorageMeter,
        repo: StorageStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._meter = meter
        self._repo = repo
        self._clock = clock or (lambda: datetime.now(UTC))

    async def project_storage_report(
        self, tenant_id: str | None = None
    ) -> ProjectStorageReport:
        """Current disk occupation per project (live MinIO measure), scoped to
        `tenant_id` (a tenant operator) or the whole platform (None)."""
        now = self._clock()
        pairs = await self._meter.discover_projects()
        projects: list[ProjectStorage] = []
        for tid, pid in sorted(pairs):
            if tenant_id is not None and tid != tenant_id:
                continue
            size = await self._meter.measure_project(tid, pid)
            projects.append(ProjectStorage(project_id=pid, tenant_id=tid, bytes=size))
        return ProjectStorageReport(
            tenant_id=tenant_id, measured_at=now.isoformat(), projects=projects
        )

    async def tenant_storage_report(self) -> TenantStorageReport:
        """Current disk occupation per TENANT (sum of each tenant's projects),
        live-measured. platform_manager cross-tenant (E-100-002 v7)."""
        now = self._clock()
        pairs = await self._meter.discover_projects()
        totals: dict[str, int] = {}
        for tid, pid in pairs:
            totals[tid] = totals.get(tid, 0) + await self._meter.measure_project(
                tid, pid
            )
        tenants = [
            TenantStorage(tenant_id=tid, bytes=b) for tid, b in sorted(totals.items())
        ]
        return TenantStorageReport(measured_at=now.isoformat(), tenants=tenants)

    async def project_series(
        self, tenant_id: str, project_id: str, window: str
    ) -> ProjectStorageSeries:
        """The snapshot time-series for one project over `window`, plus the
        current live occupation. `tenant_id` is the confirmed scope (the caller
        resolved it); the live measure uses it for the prefix."""
        now = self._clock()
        since = _window_since(window, now).isoformat()
        rows = await self._repo.series_since(project_id, since)
        current = await self._meter.measure_project(tenant_id, project_id)
        return ProjectStorageSeries(
            project_id=project_id,
            tenant_id=tenant_id,
            window=window,
            current_bytes=current,
            points=[StorageSeriesPoint(measured_at=m, bytes=b) for m, b in rows],
        )

    async def run_snapshot(self) -> StorageSnapshotResult:
        """Metering pass: discover every project, measure it, append one
        snapshot. Triggered by the periodic CronJob (platform-wide)."""
        now = self._clock()
        measured_at = now.isoformat()
        pairs = await self._meter.discover_projects()
        written = 0
        for tid, pid in pairs:
            size = await self._meter.measure_project(tid, pid)
            await self._repo.insert_snapshot(
                {
                    "tenant_id": tid,
                    "project_id": pid,
                    "measured_at": measured_at,
                    "bytes": size,
                }
            )
            written += 1
        return StorageSnapshotResult(snapshots_written=written, measured_at=measured_at)
