# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/service.py
# Description: C16 snapshot orchestration (R-900-002). For a PROJECT scope it
#              reads every project-scoped DataMap slice (Arango + MinIO, secrets
#              blanked), builds a `.tar.gz` + manifest, stores it in the backups
#              bucket, and persists a BackupRecord. Synchronous; the async HTTP
#              layer (later increment) wraps it. Tenant-scope snapshot is a
#              follow-up increment.
#
# @relation implements:R-900-002
# =============================================================================

from __future__ import annotations

import contextlib
import hashlib
import io
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, cast

from arango.cursor import Cursor

from ay_platform_core.c16_backup.archive import (
    ArangoSlices,
    MinioSlices,
    build_archive,
    parse_archive,
)
from ay_platform_core.c16_backup.data_map import (
    DATA_MAP,
    MANIFEST_VERSION,
    entries_for_scope,
)
from ay_platform_core.c16_backup.models import (
    BackupRecord,
    BackupScope,
    Origin,
    RestoreComponentCount,
    RestoreReport,
    Store,
)
from ay_platform_core.c16_backup.readers import (
    read_arango_project_slice,
    read_arango_tenant_slice,
    read_minio_project_slice,
    read_minio_tenant_slice,
)
from ay_platform_core.c16_backup.remap import remap_project, remap_slices
from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage

_ARANGO_ENTRIES = {e.name: e for e in DATA_MAP if e.store is Store.ARANGO}


class RestoreValidationError(Exception):
    """The archive cannot be restored (unsupported version, wrong scope, …)."""

if TYPE_CHECKING:
    from arango.database import StandardDatabase
    from minio import Minio


def _stamp(created_at_iso: str) -> str:
    """`_yyyymmdd_hhmmss` postfix from an ISO-8601 UTC timestamp. Falls back to
    a raw digit-squash if the timestamp is unparseable."""
    try:
        return datetime.fromisoformat(created_at_iso).strftime("%Y%m%d_%H%M%S")
    except ValueError:
        return "".join(c for c in created_at_iso if c.isdigit())[:15]


class BackupService:
    """Creates + registers logical backup archives."""

    def __init__(
        self,
        *,
        db: StandardDatabase,
        minio: Minio,
        storage: BackupStorage,
        records: BackupRecordRepository,
        platform_version: str = "",
    ) -> None:
        self._db = db
        self._minio = minio
        self._storage = storage
        self._records = records
        self._platform_version = platform_version

    def create_project_snapshot(
        self, *, tenant_id: str, project_id: str, created_by: str,
    ) -> BackupRecord:
        """Snapshot one project into a stored, registered archive (R-900-002)."""
        arango = {}
        minio = {}
        for entry in entries_for_scope(BackupScope.PROJECT):
            if entry.store is Store.ARANGO:
                arango[entry.name] = read_arango_project_slice(
                    self._db, entry, tenant_id=tenant_id, project_id=project_id,
                )
            else:
                minio[entry.name] = read_minio_project_slice(
                    self._minio, entry, tenant_id=tenant_id, project_id=project_id,
                )

        gz, manifest = build_archive(
            scope=BackupScope.PROJECT, tenant_id=tenant_id, project_id=project_id,
            arango=arango, minio=minio, platform_version=self._platform_version,
        )

        backup_id = uuid.uuid4().hex
        # Naming rule: the archive filename ends with an ISO date postfix
        # `_yyyymmdd_hhmmss` (UTC, from the manifest's created_at) for human
        # legibility; the uuid keeps it unique within the same second.
        stamp = _stamp(manifest.created_at)
        object_key = (
            f"tenant/{tenant_id}/project/{project_id}/"
            f"{backup_id}_{stamp}.tar.gz"
        )
        self._storage.ensure_bucket()
        self._storage.put(object_key, gz)

        record = BackupRecord(
            backup_id=backup_id, scope=BackupScope.PROJECT, tenant_id=tenant_id,
            project_id=project_id, created_at=manifest.created_at,
            created_by=created_by, size_bytes=len(gz), object_key=object_key,
            checksum=hashlib.sha256(gz).hexdigest(), origin=Origin.GENERATED,
            manifest_version=manifest.manifest_version,
        )
        self._records.insert(record)
        return record

    # ---- Tenant-scope snapshot (R-900-002, whole tenant) -----------------

    def _tenant_project_ids(self, tenant_id: str) -> list[str]:
        if not self._db.has_collection("c2_projects"):
            return []
        return list(cast("Cursor", self._db.aql.execute(
            "FOR d IN c2_projects FILTER d.tenant_id == @t RETURN d._key",
            bind_vars={"t": tenant_id},
        )))

    def _tenant_user_ids(self, tenant_id: str) -> list[str]:
        if not self._db.has_collection("c2_users"):
            return []
        return list(cast("Cursor", self._db.aql.execute(
            "FOR d IN c2_users FILTER d.tenant_id == @t RETURN d._key",
            bind_vars={"t": tenant_id},
        )))

    def create_tenant_snapshot(
        self, *, tenant_id: str, created_by: str,
    ) -> BackupRecord:
        """Snapshot a WHOLE tenant (tenant-level rows + every project) into one
        stored archive (R-900-002)."""
        project_ids = self._tenant_project_ids(tenant_id)
        user_ids = self._tenant_user_ids(tenant_id)
        arango = {}
        minio = {}
        for entry in entries_for_scope(BackupScope.TENANT):
            if entry.store is Store.ARANGO:
                arango[entry.name] = read_arango_tenant_slice(
                    self._db, entry, tenant_id=tenant_id, user_ids=user_ids,
                )
            else:
                minio[entry.name] = read_minio_tenant_slice(
                    self._minio, entry, tenant_id=tenant_id, project_ids=project_ids,
                )
        gz, manifest = build_archive(
            scope=BackupScope.TENANT, tenant_id=tenant_id, project_id=None,
            arango=arango, minio=minio, platform_version=self._platform_version,
        )
        backup_id = uuid.uuid4().hex
        stamp = _stamp(manifest.created_at)
        object_key = f"tenant/{tenant_id}/{backup_id}_{stamp}.tar.gz"
        self._storage.ensure_bucket()
        self._storage.put(object_key, gz)
        record = BackupRecord(
            backup_id=backup_id, scope=BackupScope.TENANT, tenant_id=tenant_id,
            project_id=None, created_at=manifest.created_at, created_by=created_by,
            size_bytes=len(gz), object_key=object_key,
            checksum=hashlib.sha256(gz).hexdigest(), origin=Origin.GENERATED,
            manifest_version=manifest.manifest_version,
        )
        self._records.insert(record)
        return record

    def restore_tenant_as_new(
        self, archive_gz: bytes, *, dry_run: bool, new_tenant_id: str | None = None,
    ) -> RestoreReport:
        """Restore a TENANT archive into a FRESH tenant (new tenant id + one new
        project id per source project) (R-900-008)."""
        manifest, arango, minio = parse_archive(archive_gz)
        if manifest.manifest_version != MANIFEST_VERSION:
            raise RestoreValidationError(
                f"unsupported manifest_version {manifest.manifest_version}"
            )
        if manifest.scope is not BackupScope.TENANT:
            raise RestoreValidationError("archive is not a TENANT-scope backup")

        target_tenant = new_tenant_id or uuid.uuid4().hex
        # One fresh project id per source project (c2_projects _key == project id).
        source_projects = [str(r["_key"]) for r in arango.get("c2_projects", [])]
        id_map = {manifest.tenant_id: target_tenant}
        new_projects: list[str] = []
        for sp in source_projects:
            np = uuid.uuid4().hex
            id_map[sp] = np
            new_projects.append(np)

        r_arango, r_minio = remap_slices(
            arango, minio, id_map=id_map, entries=_ARANGO_ENTRIES,
        )
        report = RestoreReport(
            dry_run=dry_run, committed=False, source_tenant_id=manifest.tenant_id,
            source_project_id=None, target_tenant_id=target_tenant,
            new_project_ids=new_projects, manifest_version=manifest.manifest_version,
            components=[
                RestoreComponentCount(store=e.store, name=e.name, count=e.count)
                for e in manifest.entries
            ],
        )
        if dry_run:
            return report
        try:
            self._import_arango(r_arango)
            self._import_minio(r_minio)
        except Exception:
            self._rollback_field("tenant_id", target_tenant, r_minio)
            raise
        return report.model_copy(update={"committed": True})

    def list_project_backups(
        self, *, tenant_id: str, project_id: str,
    ) -> list[BackupRecord]:
        """List a project's stored backups, newest first (R-900-005)."""
        return self._records.list_for_project(tenant_id, project_id)

    def list_tenant_backups(self, *, tenant_id: str) -> list[BackupRecord]:
        """List ALL of a tenant's backups (project + tenant scope), newest
        first (R-900-005)."""
        return self._records.list_for_tenant(tenant_id)

    def get_backup(self, backup_id: str) -> tuple[BackupRecord, bytes] | None:
        """The record + archive bytes for download (R-900-006). None if unknown."""
        record = self._records.get(backup_id)
        if record is None:
            return None
        return record, self._storage.get(record.object_key)

    def register_uploaded_archive(
        self, archive_gz: bytes, *, tenant_id: str, created_by: str,
    ) -> BackupRecord:
        """Validate + store + register an uploaded archive (R-900-007). Rejects
        a malformed/checksum-mismatched/unsupported archive (parse verifies)."""
        manifest, _arango, _minio = parse_archive(archive_gz)  # verifies integrity
        if manifest.manifest_version != MANIFEST_VERSION:
            raise RestoreValidationError(
                f"unsupported manifest_version {manifest.manifest_version}"
            )
        backup_id = uuid.uuid4().hex
        stamp = _stamp(manifest.created_at)
        object_key = f"tenant/{tenant_id}/uploaded/{backup_id}_{stamp}.tar.gz"
        self._storage.ensure_bucket()
        self._storage.put(object_key, archive_gz)
        record = BackupRecord(
            backup_id=backup_id, scope=manifest.scope, tenant_id=tenant_id,
            project_id=manifest.project_id, created_at=manifest.created_at,
            created_by=created_by, size_bytes=len(archive_gz), object_key=object_key,
            checksum=hashlib.sha256(archive_gz).hexdigest(), origin=Origin.UPLOADED,
            manifest_version=manifest.manifest_version,
        )
        self._records.insert(record)
        return record

    # ---- Restore-as-new (R-900-008/009) ----------------------------------

    def restore_project_as_new(
        self,
        archive_gz: bytes,
        *,
        target_tenant_id: str,
        dry_run: bool,
        new_project_id: str | None = None,
    ) -> RestoreReport:
        """Restore a PROJECT archive into a FRESH project id under
        `target_tenant_id`. Validates + remaps first; `dry_run` reports the plan
        without writing. On a real run, an import failure rolls the new project
        back (best-effort) so no half-populated target is left."""
        manifest, arango, minio = parse_archive(archive_gz)  # verifies integrity
        if manifest.manifest_version != MANIFEST_VERSION:
            raise RestoreValidationError(
                f"unsupported manifest_version {manifest.manifest_version} "
                f"(this platform restores {MANIFEST_VERSION})"
            )
        if manifest.scope is not BackupScope.PROJECT or manifest.project_id is None:
            raise RestoreValidationError("archive is not a PROJECT-scope backup")

        target_project = new_project_id or uuid.uuid4().hex
        r_arango, r_minio = remap_project(
            arango, minio,
            source_tenant=manifest.tenant_id, source_project=manifest.project_id,
            target_tenant=target_tenant_id, target_project=target_project,
            entries=_ARANGO_ENTRIES,
        )

        report = RestoreReport(
            dry_run=dry_run, committed=False,
            source_tenant_id=manifest.tenant_id,
            source_project_id=manifest.project_id,
            target_tenant_id=target_tenant_id, new_project_id=target_project,
            manifest_version=manifest.manifest_version,
            components=[
                RestoreComponentCount(store=e.store, name=e.name, count=e.count)
                for e in manifest.entries
            ],
        )
        if dry_run:
            return report

        try:
            self._import_arango(r_arango)
            self._import_minio(r_minio)
        except Exception:
            self._rollback_field("project_id", target_project, r_minio)
            raise
        return report.model_copy(update={"committed": True})

    def _import_arango(self, arango: ArangoSlices) -> None:
        for coll, rows in arango.items():
            if not rows:
                continue
            is_edge = any("_from" in r for r in rows)
            if not self._db.has_collection(coll):
                self._db.create_collection(coll, edge=is_edge)
            self._db.collection(coll).insert_many(rows, overwrite=False)

    def _import_minio(self, minio: MinioSlices) -> None:
        for bucket, objects in minio.items():
            if not objects:
                continue
            if not self._minio.bucket_exists(bucket):
                self._minio.make_bucket(bucket)
            for key, blob in objects.items():
                self._minio.put_object(bucket, key, io.BytesIO(blob), length=len(blob))

    def _rollback_field(
        self, field: str, value: str, minio: MinioSlices,
    ) -> None:
        """Best-effort removal of a half-imported target (restore-as-new never
        leaves a partial project/tenant). `field` is a code-owned column name
        (`project_id` or `tenant_id`)."""
        for coll in _ARANGO_ENTRIES:
            if not self._db.has_collection(coll):
                continue
            with contextlib.suppress(Exception):
                self._db.aql.execute(
                    f"FOR d IN {coll} FILTER d.{field} == @v REMOVE d IN {coll}",
                    bind_vars={"v": value},
                )
        for bucket, objects in minio.items():
            for key in objects:
                with contextlib.suppress(Exception):
                    self._minio.remove_object(bucket, key)
