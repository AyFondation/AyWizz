# =============================================================================
# File: minio_storage.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c7_memory/storage/minio_storage.py
# Description: MinIO blob storage for uploaded sources (Phase B of v1
#              functional plan). The raw file bytes are persisted under
#              `sources/{tenant_id}/{project_id}/{source_id}{.ext?}`;
#              the parser extracts text into Arango chunks, but the
#              original file remains available for re-parse, audit,
#              and download.
#
#              v2 adds JSON processing-artifact helpers (R-400-207):
#              chunks.json / kg.json under
#              `sources/{tenant}/{project}/{source_id}/<name>` so the
#              vector + graph stores can be rebuilt by replay without
#              re-embedding or re-invoking the LLM.
#
#              Pattern: same as C5/C6 storage layers — sync `minio` SDK
#              wrapped in `asyncio.to_thread` for the FastAPI event
#              loop's safety.
#
# @relation implements:R-400-207
# =============================================================================

from __future__ import annotations

import asyncio
import io
import mimetypes
from dataclasses import dataclass

from minio import Minio
from minio.error import S3Error


@dataclass(frozen=True, slots=True)
class BlobMetadata:
    path: str
    size: int
    etag: str
    content_type: str


class StorageError(RuntimeError):
    """Raised on non-recoverable MinIO failures."""


class MemorySourceStorage:
    """Blob storage facade for C7 uploaded sources.

    All paths are tenant + project + source-id scoped so cross-tenant
    listing of the bucket can never leak. A best-effort extension is
    appended to the object name based on the MIME type so admins
    inspecting MinIO see human-recognisable filenames.
    """

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def _ensure_bucket_sync(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self._ensure_bucket_sync)

    @staticmethod
    def source_path(
        tenant_id: str, project_id: str, source_id: str, mime_type: str
    ) -> str:
        ext = mimetypes.guess_extension(mime_type) or ""
        return f"sources/{tenant_id}/{project_id}/{source_id}{ext}"

    def _put_object_sync(
        self, path: str, data: bytes, content_type: str
    ) -> BlobMetadata:
        try:
            stream = io.BytesIO(data)
            result = self._client.put_object(
                self._bucket,
                path,
                data=stream,
                length=len(data),
                content_type=content_type,
            )
        except S3Error as exc:
            raise StorageError(f"MinIO put failed for {path}: {exc}") from exc
        return BlobMetadata(
            path=path,
            size=len(data),
            etag=result.etag or "",
            content_type=content_type,
        )

    async def put_source_blob(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        data: bytes,
        mime_type: str,
    ) -> BlobMetadata:
        """Persist `data` under the deterministic source path. Returns
        metadata including ETag for audit / verification."""
        path = self.source_path(tenant_id, project_id, source_id, mime_type)
        return await asyncio.to_thread(
            self._put_object_sync, path, data, mime_type,
        )

    def _get_object_sync(self, path: str) -> bytes:
        try:
            response = self._client.get_object(self._bucket, path)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise FileNotFoundError(path) from exc
            raise StorageError(f"MinIO get failed for {path}: {exc}") from exc

    async def get_source_blob(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        mime_type: str,
    ) -> bytes:
        path = self.source_path(tenant_id, project_id, source_id, mime_type)
        return await asyncio.to_thread(self._get_object_sync, path)

    def _delete_object_sync(self, path: str) -> None:
        try:
            self._client.remove_object(self._bucket, path)
        except S3Error as exc:
            raise StorageError(
                f"MinIO delete failed for {path}: {exc}"
            ) from exc

    async def delete_source_blob(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        mime_type: str,
    ) -> None:
        path = self.source_path(tenant_id, project_id, source_id, mime_type)
        await asyncio.to_thread(self._delete_object_sync, path)

    # ------------------------------------------------------------------
    # Processing-artifact helpers (R-400-207) — chunks.json / kg.json
    # ------------------------------------------------------------------

    @staticmethod
    def artifact_path(
        tenant_id: str, project_id: str, source_id: str, name: str
    ) -> str:
        """Deterministic path for a per-source JSON processing artifact."""
        return f"sources/{tenant_id}/{project_id}/{source_id}/{name}"

    async def put_artifact(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        name: str,
        data: bytes,
    ) -> BlobMetadata:
        """Persist a JSON processing artifact (e.g. chunks.json, kg.json)
        so the source's contribution to the vector + graph stores can be
        replayed without re-embedding or re-invoking the LLM."""
        path = self.artifact_path(tenant_id, project_id, source_id, name)
        return await asyncio.to_thread(
            self._put_object_sync, path, data, "application/json",
        )

    async def get_artifact(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        name: str,
    ) -> bytes:
        """Read a JSON processing artifact. Raises ``FileNotFoundError``
        when the artifact does not exist (e.g. KG never extracted)."""
        path = self.artifact_path(tenant_id, project_id, source_id, name)
        return await asyncio.to_thread(self._get_object_sync, path)
