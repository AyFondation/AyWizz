# src/storage/minio_writer.py — v1
"""MinIO writer implementing BaseOutputWriter (D-020 v1 §B.b / R-100-125 v2 §6).

C13 writes ALL its outputs to MinIO via the S3-API surface (boto3). This
file replaces the stripped `local_writer.py` and `s3_writer.py`. The
layout is fixed by R-400-220 v2:

  {bucket}/{tenant_id}/{project_id}/{source_id}/runs/{run_id}/
    00_metadata/...
    01_extraction/...
    02_chunks/{chunks.jsonl, embeddings.jsonl, ...}
    status.json

This writer is path-agnostic — it accepts the full key string from the
caller and turns it into bucket/key for S3. The bucket is provided once
at construction time (`Settings.output_minio_bucket`); the prefix in
the key is the rest of the path.

In-cluster: endpoint = `http://c10-minio:9000`. Standalone dev:
`http://localhost:9000`. The endpoint, region, and credentials are read
from settings (R-100-118 single-source).
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from ayextractor.storage.base_output_writer import BaseOutputWriter

logger = logging.getLogger(__name__)


class MinioWriter(BaseOutputWriter):
    """S3-API writer (MinIO, AWS S3, any compatible store).

    The writer is **async-safe by delegation**: boto3 is synchronous, so
    every public method runs the underlying boto3 call inside
    `asyncio.to_thread()`. Boto3 is thread-safe at the client level, so
    concurrent calls from different tasks are safe.

    Symlinks have no S3 equivalent; `create_symlink` writes a tiny
    `.symlink` marker object containing the target — sufficient for
    AyExtractor's "latest" pointer pattern (R-100-125 v2 §6 keeps the
    symlink semantics local-only when running standalone CLI; in-cluster
    the n8n workflow tracks the latest run via the `run_manifest`).
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        prefix: str = "",
    ) -> None:
        """Initialise the writer.

        Args:
            bucket: MinIO bucket name (e.g. `c13-extractor-artifacts`).
                MUST exist or be created out-of-band; this writer does NOT
                auto-create the bucket (R-100-118 dedicated runtime user
                has no `s3:CreateBucket` permission).
            endpoint_url: S3 API endpoint. In-cluster:
                `http://c10-minio:9000`. Standalone: `http://localhost:9000`.
            access_key: S3 access key (dedicated runtime user — R-100-118).
            secret_key: S3 secret key (dedicated runtime user).
            region: AWS region or `us-east-1` for MinIO.
            prefix: Optional global key prefix prepended to every path.
                Useful for multi-tenant deployments sharing one bucket.
                Default = no prefix.
        """
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._prefix = prefix.rstrip("/")

    def _client(self) -> Any:
        """Lazy boto3 client — keeps the import inside the method (extras = `minio`)."""
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    def _key(self, path: str) -> str:
        """Apply the global prefix to a path and strip leading slashes."""
        normalised = path.lstrip("/")
        if self._prefix:
            return f"{self._prefix}/{normalised}"
        return normalised

    async def write(self, path: str, content: bytes | str) -> None:
        """Write content to the given key.

        bytes → put_object with binary body. str → encoded as UTF-8.
        """
        body = content.encode("utf-8") if isinstance(content, str) else content
        key = self._key(path)
        await asyncio.to_thread(
            self._client().put_object,
            Bucket=self._bucket,
            Key=key,
            Body=body,
        )
        logger.debug("MinIO put: s3://%s/%s (%d bytes)", self._bucket, key, len(body))

    async def read(self, path: str) -> bytes:
        """Read raw bytes from the given key."""
        key = self._key(path)
        resp = await asyncio.to_thread(
            self._client().get_object, Bucket=self._bucket, Key=key
        )
        body: bytes = await asyncio.to_thread(resp["Body"].read)
        return body

    async def exists(self, path: str) -> bool:
        """Return True if an object exists at the given key."""
        key = self._key(path)
        client = self._client()
        try:
            await asyncio.to_thread(client.head_object, Bucket=self._bucket, Key=key)
            return True
        except client.exceptions.ClientError as exc:  # type: ignore[attr-defined]
            # 404 = missing; anything else = re-raise.
            err_code = exc.response.get("Error", {}).get("Code", "")
            if err_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    async def copy(self, src: str, dst: str) -> None:
        """Server-side copy from src key to dst key (no roundtrip via client)."""
        src_key = self._key(src)
        dst_key = self._key(dst)
        await asyncio.to_thread(
            self._client().copy_object,
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": src_key},
            Key=dst_key,
        )
        logger.debug("MinIO copy: s3://%s/%s → s3://%s/%s", self._bucket, src_key, self._bucket, dst_key)

    async def create_symlink(self, target: str, link: str) -> None:
        """S3 has no symlinks — write a tiny marker object containing the target key.

        AyExtractor's only symlink use case is the "latest" pointer to the
        most recent run. In-cluster, n8n reads `run_manifest.json`
        directly and doesn't depend on this marker, so the implementation
        is best-effort: a `*.symlink` object whose body is the resolved
        target key.
        """
        marker_key = self._key(f"{link}.symlink")
        await asyncio.to_thread(
            self._client().put_object,
            Bucket=self._bucket,
            Key=marker_key,
            Body=self._key(target).encode("utf-8"),
            ContentType="text/plain",
        )

    async def list_dir(self, path: str) -> list[str]:
        """List objects under the given prefix.

        S3 does not have directories — this returns all keys starting with
        `path`. The caller is responsible for filtering or recursing
        further. Returned keys are RELATIVE to the writer's prefix
        (R-100-125 v2 §6 — the writer hides the prefix from callers).
        """
        list_prefix = self._key(path)
        if list_prefix and not list_prefix.endswith("/"):
            list_prefix = f"{list_prefix}/"
        client = self._client()
        keys: list[str] = []
        paginator = await asyncio.to_thread(client.get_paginator, "list_objects_v2")
        pages = await asyncio.to_thread(
            lambda: list(paginator.paginate(Bucket=self._bucket, Prefix=list_prefix))
        )
        for page in pages:
            for obj in page.get("Contents", []):
                full_key: str = obj["Key"]
                # Strip the global prefix to return relative paths.
                if self._prefix and full_key.startswith(f"{self._prefix}/"):
                    full_key = full_key[len(self._prefix) + 1 :]
                keys.append(full_key)
        return keys
