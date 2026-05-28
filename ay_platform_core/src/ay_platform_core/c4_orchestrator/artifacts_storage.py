# =============================================================================
# File: artifacts_storage.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_storage.py
# Description: MinIO read-only adapter for the project-artifacts surface.
#              Reads blobs under the convention
#              `{bucket}/c4-artifacts/{tenant_id}/{project_id}/{run_id}/...`
#              (R-200-130). The C4 service is the only authorised writer ;
#              the UX consumes exclusively through this adapter (no link
#              to MinIO console exposed — R-200-133).
#
#              Pattern : sync `minio` SDK wrapped in `asyncio.to_thread`
#              for the FastAPI event loop's safety. Same shape as
#              C7's `MemorySourceStorage` and C5/C6's storage layers.
#
# @relation implements:R-200-130
# @relation implements:R-200-131
# @relation implements:R-200-133
# =============================================================================

from __future__ import annotations

import asyncio
import io
import mimetypes
from dataclasses import dataclass

from minio import Minio
from minio.error import S3Error


@dataclass(frozen=True, slots=True)
class ArtifactBlob:
    """Bytes + content-type the router streams back. Kept tiny on
    purpose — large files would blow up RAM. v1 reads everything
    in-memory ; v2 SHALL switch to a streaming response when file
    sizes warrant (≥ a few MB). For Pass 1 (Code source / DocGen)
    typical files are source + docs in the kB range — non-issue."""

    data: bytes
    content_type: str


class ArtifactStorageError(RuntimeError):
    """Raised on non-recoverable MinIO failures (network, permissions).
    `FileNotFoundError` is raised separately for the 'object is
    missing' case so the router can map it to a 404."""


class ArtifactStorage:
    """Read-only MinIO adapter for the artifacts REST surface.

    The convention `bucket/c4-artifacts/{tenant}/{project}/{run}/{path}`
    is enforced via the `_object_name` helper — callers pass
    (tenant_id, project_id, run_id, relative_path) and the helper
    builds the full key. Reverse function `_split_key` is used by
    `list_tree` to rebuild the relative path from a listing.
    """

    PREFIX_ROOT = "c4-artifacts"

    def __init__(self, client: Minio, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @classmethod
    def _run_prefix(cls, tenant_id: str, project_id: str, run_id: str) -> str:
        """Build the MinIO key prefix for one run. Trailing slash so
        `list_objects(prefix=...)` is bounded by the directory."""
        return f"{cls.PREFIX_ROOT}/{tenant_id}/{project_id}/{run_id}/"

    @classmethod
    def _object_name(
        cls, tenant_id: str, project_id: str, run_id: str, relative_path: str,
    ) -> str:
        """Resolve a (tenant, project, run, rel_path) tuple to the full
        MinIO object name. Defensive : rejects `..`, leading `/`, and
        Windows-style backslashes per R-200-130 — those are signs of
        either a bug or a path-traversal attempt and SHALL never
        reach the storage layer."""
        if relative_path.startswith("/"):
            raise ValueError("relative_path must NOT start with '/'")
        if "\\" in relative_path:
            raise ValueError("relative_path must use POSIX forward slashes")
        parts = relative_path.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError("relative_path contains forbidden segments")
        return cls._run_prefix(tenant_id, project_id, run_id) + relative_path

    # ------------------------------------------------------------------
    # Bucket initialisation — idempotent ; called at app startup so a
    # fresh dev stack just works without an out-of-band `mc mb` step.
    # ------------------------------------------------------------------

    def _ensure_bucket_sync(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def ensure_bucket(self) -> None:
        await asyncio.to_thread(self._ensure_bucket_sync)

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def _list_tree_sync(
        self, tenant_id: str, project_id: str, run_id: str,
    ) -> list[tuple[str, int]]:
        """Return (relative_path, size_bytes) for every blob under
        the run prefix, recursive. The repository pairs this with
        the run-level metadata to compute `file_count` + `total_bytes`
        once at write time ; for reads the UX hits the Arango
        document, not MinIO."""
        prefix = self._run_prefix(tenant_id, project_id, run_id)
        try:
            objects = self._client.list_objects(
                self._bucket, prefix=prefix, recursive=True,
            )
            out: list[tuple[str, int]] = []
            for obj in objects:
                # `obj.is_dir` skips MinIO's pseudo-folders (rare with
                # recursive=True but harmless). object_name starts with
                # the prefix — strip it to get the relative path the
                # UX displays.
                if getattr(obj, "is_dir", False):
                    continue
                name = obj.object_name or ""
                if not name.startswith(prefix):
                    continue
                rel = name[len(prefix):]
                size = int(getattr(obj, "size", 0) or 0)
                out.append((rel, size))
            return out
        except S3Error as exc:
            raise ArtifactStorageError(
                f"MinIO list failed for run {run_id!r}: {exc}",
            ) from exc

    async def list_tree(
        self, tenant_id: str, project_id: str, run_id: str,
    ) -> list[tuple[str, int]]:
        return await asyncio.to_thread(
            self._list_tree_sync, tenant_id, project_id, run_id,
        )

    def _get_blob_sync(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        relative_path: str,
    ) -> ArtifactBlob:
        # Path-shape errors (ValueError from `_object_name`) propagate to the
        # router layer as 400 ; raising FileNotFoundError here would mask a
        # programming bug as a 404, which is misleading. Let `_object_name`
        # raise.
        key = self._object_name(tenant_id, project_id, run_id, relative_path)
        try:
            response = self._client.get_object(self._bucket, key)
            try:
                data = response.read()
                content_type = (
                    response.headers.get("Content-Type")
                    or mimetypes.guess_type(relative_path)[0]
                    or "application/octet-stream"
                )
                return ArtifactBlob(data=data, content_type=content_type)
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                raise FileNotFoundError(
                    f"artifact not found at {relative_path!r}",
                ) from exc
            raise ArtifactStorageError(
                f"MinIO get_object failed for {key!r}: {exc}",
            ) from exc

    async def get_blob(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        relative_path: str,
    ) -> ArtifactBlob:
        return await asyncio.to_thread(
            self._get_blob_sync, tenant_id, project_id, run_id, relative_path,
        )

    # ------------------------------------------------------------------
    # Write API — used by the seeder and (eventually) the C4 pipeline.
    # The UX SHALL NOT trigger writes ; the router exposes no PUT/POST.
    # ------------------------------------------------------------------

    def _put_blob_sync(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        relative_path: str,
        data: bytes,
        content_type: str,
    ) -> None:
        key = self._object_name(tenant_id, project_id, run_id, relative_path)
        try:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except S3Error as exc:
            raise ArtifactStorageError(
                f"MinIO put_object failed for {key!r}: {exc}",
            ) from exc

    async def put_blob(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        relative_path: str,
        data: bytes,
        content_type: str,
    ) -> None:
        await asyncio.to_thread(
            self._put_blob_sync,
            tenant_id, project_id, run_id, relative_path, data, content_type,
        )

    def _delete_blob_sync(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        relative_path: str,
    ) -> None:
        """Remove one object. MinIO's `remove_object` is idempotent —
        deleting a missing key is a no-op, not an error. We mirror that
        here (the documents DELETE endpoint maps a genuinely-missing
        path to 404 by pre-checking with a list, not by relying on a
        delete error)."""
        key = self._object_name(tenant_id, project_id, run_id, relative_path)
        try:
            self._client.remove_object(self._bucket, key)
        except S3Error as exc:
            raise ArtifactStorageError(
                f"MinIO remove_object failed for {key!r}: {exc}",
            ) from exc

    async def delete_blob(
        self,
        tenant_id: str,
        project_id: str,
        run_id: str,
        relative_path: str,
    ) -> None:
        await asyncio.to_thread(
            self._delete_blob_sync,
            tenant_id, project_id, run_id, relative_path,
        )
