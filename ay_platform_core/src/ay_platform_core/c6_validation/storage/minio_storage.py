# =============================================================================
# File: minio_storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/storage/minio_storage.py
# Description: MinIO snapshot store for C6 validation reports. Each
#              completed run is archived as an immutable JSON snapshot at
#              `validation-reports/<project_id>/<run_id>.json` (R-700-013).
#
# @relation implements:R-700-013
# =============================================================================

from __future__ import annotations

import asyncio
import io

from minio import Minio
from minio.error import S3Error


class SnapshotStorageError(RuntimeError):
    """Raised on non-recoverable MinIO failures writing a run snapshot."""


class ValidationSnapshotStorage:
    """Async MinIO facade for validation-report snapshots."""

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _ensure_bucket_sync(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self._ensure_bucket_sync)

    @staticmethod
    def snapshot_path(project_id: str, run_id: str) -> str:
        return f"validation-reports/{project_id}/{run_id}.json"

    def _put_snapshot_sync(self, path: str, data: bytes) -> None:
        try:
            stream = io.BytesIO(data)
            self._client.put_object(
                self._bucket,
                path,
                data=stream,
                length=len(data),
                content_type="application/json",
            )
        except S3Error as exc:
            raise SnapshotStorageError(
                f"MinIO put failed for {path}: {exc}"
            ) from exc

    async def put_snapshot(self, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._put_snapshot_sync, path, data)

    def _get_snapshot_sync(self, path: str) -> bytes:
        try:
            response = self._client.get_object(self._bucket, path)
            try:
                data: bytes = response.read()
                return data
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise FileNotFoundError(path) from exc
            raise SnapshotStorageError(
                f"MinIO get failed for {path}: {exc}"
            ) from exc

    async def get_snapshot(self, path: str) -> bytes:
        return await asyncio.to_thread(self._get_snapshot_sync, path)


def make_snapshot_storage(
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    *,
    secure: bool = False,
) -> ValidationSnapshotStorage:
    """Factory used by the FastAPI lifespan and integration tests."""
    client = Minio(
        endpoint, access_key=access_key, secret_key=secret_key, secure=secure
    )
    return ValidationSnapshotStorage(client, bucket)
