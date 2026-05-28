# =============================================================================
# File: minio_storage.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/storage/minio_storage.py
# Description: MinIO source-of-truth storage for C5.
#              Async wrappers around the sync `minio` client via
#              asyncio.to_thread (pattern established by C2/C3).
#
# @relation implements:R-300-010
# @relation implements:R-300-031
# @relation implements:R-300-034
# @relation implements:R-300-062
# =============================================================================

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import cast

from minio import Minio
from minio.error import S3Error


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """Minimal metadata exposed to the service layer."""

    path: str
    size: int
    etag: str
    content_hash: str | None = None


class StorageError(RuntimeError):
    """Raised on non-recoverable MinIO failures (R-300-062)."""


class RequirementsStorage:
    """Async MinIO facade dedicated to C5 corpus paths.

    Paths are scoped by R-300-010:
      - projects/<pid>/requirements/<doc-slug>.md
      - projects/<pid>/requirements/_history/<doc-slug>/<eid>@v<N>.md
      - projects/<pid>/requirements/_deleted/<doc-slug>.<ts>.md
      - platform/requirements/<doc-slug>.md
    """

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_bucket_sync(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self._ensure_bucket_sync)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def document_path(project_id: str, slug: str) -> str:
        if project_id == "platform":
            return f"platform/requirements/{slug}.md"
        return f"projects/{project_id}/requirements/{slug}.md"

    @staticmethod
    def history_path(project_id: str, doc_slug: str, entity_id: str, version: int) -> str:
        return (
            f"projects/{project_id}/requirements/_history/"
            f"{doc_slug}/{entity_id}@v{version}.md"
        )

    @staticmethod
    def deleted_path(project_id: str, slug: str, timestamp: str) -> str:
        return f"projects/{project_id}/requirements/_deleted/{slug}.{timestamp}.md"

    # ------------------------------------------------------------------
    # Read / write / delete operations
    # ------------------------------------------------------------------

    def _get_object_sync(self, path: str) -> bytes:
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
            raise StorageError(f"MinIO get failed for {path}: {exc}") from exc

    async def get_document(self, path: str) -> bytes:
        """Read a raw document by absolute object path. Raises FileNotFoundError."""
        return await asyncio.to_thread(self._get_object_sync, path)

    def _put_object_sync(self, path: str, data: bytes) -> None:
        try:
            stream = io.BytesIO(data)
            self._client.put_object(
                self._bucket,
                path,
                data=stream,
                length=len(data),
                content_type="text/markdown",
            )
        except S3Error as exc:
            raise StorageError(f"MinIO put failed for {path}: {exc}") from exc

    async def put_document(self, path: str, data: bytes) -> None:
        """Write (or overwrite) a document at the given path."""
        await asyncio.to_thread(self._put_object_sync, path, data)

    def _delete_object_sync(self, path: str) -> None:
        try:
            self._client.remove_object(self._bucket, path)
        except S3Error as exc:
            raise StorageError(f"MinIO delete failed for {path}: {exc}") from exc

    async def delete_document(self, path: str) -> None:
        """Remove a document by absolute path. Rejects `_history/` writes is
        handled at the service layer — here we only execute."""
        await asyncio.to_thread(self._delete_object_sync, path)

    def _list_objects_sync(self, prefix: str) -> list[ObjectMetadata]:
        results: list[ObjectMetadata] = []
        for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
            results.append(
                ObjectMetadata(
                    path=cast(str, obj.object_name),
                    size=cast(int, obj.size or 0),
                    etag=cast(str, obj.etag or ""),
                )
            )
        return results

    async def list_objects(self, prefix: str) -> list[ObjectMetadata]:
        return await asyncio.to_thread(self._list_objects_sync, prefix)


def make_storage(
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    *,
    secure: bool = False,
) -> RequirementsStorage:
    """Factory used by the FastAPI lifespan and integration tests."""
    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    return RequirementsStorage(client, bucket)


