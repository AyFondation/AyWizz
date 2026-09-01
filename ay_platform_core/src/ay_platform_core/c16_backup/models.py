# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/models.py
# Description: Pydantic v2 contracts for C16 Backup/Restore (900-SPEC):
#              E-900-001 DataMapEntry, E-900-002 BackupManifest (+ ManifestEntry),
#              E-900-003 BackupRecord, plus the Store / BackupScope / Origin
#              enums. No secret ever appears here (R-900-010).
#
# @relation implements:E-900-001
# @relation implements:E-900-002
# @relation implements:E-900-003
# =============================================================================

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Store(StrEnum):
    """The two persistent stores a backup touches (D-022 v1 scope)."""

    ARANGO = "arango"
    MINIO = "minio"


class BackupScope(StrEnum):
    """Granularity of a snapshot AND of a DataMap entry.

    - `TENANT`: tenant-level data (the tenant row, users, tenant catalogue).
      Present ONLY in a tenant-scope snapshot.
    - `PROJECT`: project-level data (carries `project_id`). Present in BOTH a
      project-scope snapshot (for that project) and a tenant-scope snapshot
      (all projects).
    """

    TENANT = "tenant"
    PROJECT = "project"


class Origin(StrEnum):
    """How a stored archive came to exist."""

    GENERATED = "generated"
    UPLOADED = "uploaded"


class KeyStrategy(StrEnum):
    """How a collection's `_key` is remapped on restore-as-new (R-900-008).

    - `OPAQUE`: the key is arbitrary (uuid, hash) — a FRESH key is minted and
      every reference (`_from`/`_to`) is rewired through the old→new map. Safe
      against collisions ; the app must not rely on `_key` equalling an id.
    - `PROJECT_ID`: the key IS the project id — the new key is the new project
      id (the app looks the row up by that id).
    - `TENANT_ID`: the key IS the tenant id — new key is the new tenant id.
    - `COMPOSITE`: the key EMBEDS the tenant/project ids as substrings (e.g.
      `{tenant}:{project}:{x}`) — the ids are substring-replaced in place.
    """

    OPAQUE = "opaque"
    PROJECT_ID = "project_id"
    TENANT_ID = "tenant_id"
    COMPOSITE = "composite"


class DataMapEntry(BaseModel):
    """E-900-001 — one backed-up store slice. The DataMap is the list of these
    (see `data_map.py`); it is the single source of truth for what a snapshot
    contains and how a restore re-hydrates it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    store: Store
    name: str = Field(min_length=1)
    """Arango collection name, or MinIO bucket name."""
    component: str = Field(min_length=1)
    """Owning component id, e.g. `c7_memory`."""
    scope: BackupScope
    """The scope this entry belongs to (see BackupScope)."""
    tenant_scoped: bool = True
    """Rows/objects carry a `tenant_id` (Arango) or a tenant key segment (MinIO)."""
    project_scoped: bool = False
    """Rows/objects carry a `project_id` (Arango) or a project key segment (MinIO)."""
    schema_version: int = Field(default=1, ge=1)
    key_strategy: KeyStrategy = KeyStrategy.OPAQUE
    """How `_key` is remapped on restore-as-new (R-900-008). Arango only."""
    secret_fields: tuple[str, ...] = ()
    """Fields blanked on export (R-900-010). Never dropped — blanked in place."""
    key_note: str = ""
    """MinIO only: how object keys encode tenant/project (documented, not parsed here)."""
    note: str = ""


class ExcludedStore(BaseModel):
    """A store deliberately NOT backed up, with the reason. Kept alongside the
    DataMap so the completeness coherence test (R-900-001) can assert every real
    store is either mapped or explicitly excluded — nothing escapes silently."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    store: Store
    name: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ManifestEntry(BaseModel):
    """One store slice inside an archive: its row/object count + checksum."""

    model_config = ConfigDict(extra="forbid")

    store: Store
    name: str
    count: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class BackupManifest(BaseModel):
    """E-900-002 — the `manifest.json` at an archive root. Validated on upload
    (R-900-007) and restore (R-900-009)."""

    model_config = ConfigDict(extra="forbid")

    manifest_version: int = Field(ge=1)
    scope: BackupScope
    tenant_id: str = Field(min_length=1)
    project_id: str | None = None
    created_at: str
    platform_version: str = ""
    entries: list[ManifestEntry] = Field(default_factory=list)


class RestoreComponentCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: Store
    name: str
    count: int = Field(ge=0)


class RestoreReport(BaseModel):
    """Result of a restore-as-new (R-900-008/009). In `dry_run` nothing was
    written and `committed` is False; the report still lists exactly what WOULD
    be restored (components + counts + the minted target ids)."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    committed: bool
    source_tenant_id: str
    source_project_id: str | None
    target_tenant_id: str
    new_project_id: str | None = None
    """The minted project id for a PROJECT restore; None for a TENANT restore
    (which mints one new project id per source project)."""
    new_project_ids: list[str] = Field(default_factory=list)
    """The minted project ids for a TENANT restore (empty for a project restore)."""
    manifest_version: int
    components: list[RestoreComponentCount] = Field(default_factory=list)


class RestoreRequest(BaseModel):
    """Body of a restore-as-new call: `dry_run` reports the plan without
    writing; `new_project_id` optionally pins the target project id (else one
    is minted)."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = False
    new_project_id: str | None = Field(default=None, min_length=2, max_length=64)


class BackupRecord(BaseModel):
    """E-900-003 — the registry row for one stored archive (Arango
    `backup_records`). Holds metadata + a pointer, never the archive bytes."""

    model_config = ConfigDict(extra="forbid")

    backup_id: str = Field(min_length=1)
    scope: BackupScope
    tenant_id: str = Field(min_length=1)
    project_id: str | None = None
    created_at: str
    created_by: str
    size_bytes: int = Field(ge=0)
    object_key: str = Field(min_length=1)
    checksum: str = Field(min_length=64, max_length=64)
    origin: Origin
    manifest_version: int = Field(ge=1)
