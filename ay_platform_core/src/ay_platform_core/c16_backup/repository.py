# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/repository.py
# Description: Persistence for C16: the `backup_records` registry (Arango,
#              E-900-003) + the dedicated `backups` bucket storage (MinIO,
#              R-900-004). Both are synchronous; the async HTTP layer (a later
#              increment) wraps them in `asyncio.to_thread`.
#
# @relation implements:E-900-003
# @relation implements:R-900-004
# =============================================================================

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from arango.cursor import Cursor

from ay_platform_core.c16_backup.models import BackupRecord

if TYPE_CHECKING:
    from arango.database import StandardDatabase
    from minio import Minio

COLL_BACKUP_RECORDS = "backup_records"


class BackupRecordRepository:
    """Registry of stored archives (metadata + pointer, never the bytes)."""

    def __init__(self, db: StandardDatabase) -> None:
        self._db = db

    def ensure_collections(self) -> None:
        if not self._db.has_collection(COLL_BACKUP_RECORDS):
            self._db.create_collection(COLL_BACKUP_RECORDS)

    def insert(self, record: BackupRecord) -> None:
        doc = record.model_dump()
        doc["_key"] = record.backup_id
        self._db.collection(COLL_BACKUP_RECORDS).insert(doc, overwrite=True)

    def get(self, backup_id: str) -> BackupRecord | None:
        doc = cast("dict[str, Any] | None",
                   self._db.collection(COLL_BACKUP_RECORDS).get(backup_id))
        return None if doc is None else BackupRecord.model_validate(_clean(doc))

    def list_for_tenant(self, tenant_id: str) -> list[BackupRecord]:
        cursor = cast("Cursor", self._db.aql.execute(
            f"FOR r IN {COLL_BACKUP_RECORDS} "
            "FILTER r.tenant_id == @t SORT r.created_at DESC RETURN r",
            bind_vars={"t": tenant_id},
        ))
        return [BackupRecord.model_validate(_clean(d)) for d in cursor]

    def list_for_project(self, tenant_id: str, project_id: str) -> list[BackupRecord]:
        cursor = cast("Cursor", self._db.aql.execute(
            f"FOR r IN {COLL_BACKUP_RECORDS} "
            "FILTER r.tenant_id == @t AND r.project_id == @p "
            "SORT r.created_at DESC RETURN r",
            bind_vars={"t": tenant_id, "p": project_id},
        ))
        return [BackupRecord.model_validate(_clean(d)) for d in cursor]


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


class BackupStorage:
    """The dedicated `backups` MinIO bucket (R-900-004). Stores/reads archive
    bytes by object key."""

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put(self, object_key: str, data: bytes) -> None:
        import io  # noqa: PLC0415 - cold path

        self._client.put_object(
            self._bucket, object_key, io.BytesIO(data), length=len(data),
            content_type="application/gzip",
        )

    def get(self, object_key: str) -> bytes:
        resp = self._client.get_object(self._bucket, object_key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
