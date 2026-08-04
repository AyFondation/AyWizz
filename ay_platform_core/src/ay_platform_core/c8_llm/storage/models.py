# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/storage/models.py
# Description: Pydantic contracts for the per-project storage dashboards
#              (E-100-002 v7). A `ProjectStorageReport` is the CURRENT
#              occupation per project (live MinIO measure); a
#              `ProjectStorageSeries` is the historical time-series from the
#              periodic metering snapshots (`storage_snapshots`).
# =============================================================================

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# Series windows for the storage dashboards — same calendar anchors as the
# project cost report.
STORAGE_WINDOWS: tuple[str, ...] = (
    "day",
    "week",
    "month",
    "quarter",
    "semester",
    "year",
)


class ProjectStorage(BaseModel):
    """Current disk occupation of one project (sum of MinIO object sizes under
    its artifact prefix), in bytes."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    tenant_id: str
    bytes: int


class ProjectStorageReport(BaseModel):
    """Current per-project disk occupation, scoped to a tenant (admin) or the
    whole platform (platform_manager). `measured_at` is the live-measure
    instant."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = None
    measured_at: str
    projects: list[ProjectStorage]


class TenantStorage(BaseModel):
    """Current disk occupation of one tenant (sum of its projects), in bytes."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    bytes: int


class TenantStorageReport(BaseModel):
    """Current per-tenant disk occupation (platform_manager, cross-tenant)."""

    model_config = ConfigDict(extra="forbid")

    measured_at: str
    tenants: list[TenantStorage]


class StorageSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measured_at: str
    bytes: int


class ProjectStorageSeries(BaseModel):
    """One project's storage time-series over a window (from snapshots), plus
    the current live-measured occupation."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    tenant_id: str
    window: str
    current_bytes: int
    points: list[StorageSeriesPoint]


class StorageSnapshotResult(BaseModel):
    """Outcome of a metering pass — how many project snapshots were written."""

    model_config = ConfigDict(extra="forbid")

    snapshots_written: int
    measured_at: str
