# src/storage/writer_factory.py — v4
"""Factory: instantiate output writer from configuration.

D-020 session 3: MinIO writer wired. `local` / `s3` legacy writers were
stripped in session 2; the factory now resolves the sole supported
writer (`minio`) and surfaces a clear error for legacy values.
"""

from __future__ import annotations

from ayextractor.config.settings import Settings
from ayextractor.storage.base_output_writer import BaseOutputWriter


def create_writer(settings: Settings) -> BaseOutputWriter:
    """Create the configured output writer.

    Args:
        settings: Application settings (`OUTPUT_WRITER` env var, default
            `"minio"`).

    Returns:
        BaseOutputWriter instance.

    Raises:
        NotImplementedError: If `OUTPUT_WRITER` is the legacy `local` or
            `s3` (stripped by D-020 v1 session 2).
        ValueError: If `OUTPUT_WRITER` is unknown.
    """
    if settings.output_writer == "minio":
        from ayextractor.storage.minio_writer import MinioWriter

        if not settings.output_minio_bucket:
            raise ValueError(
                "OUTPUT_MINIO_BUCKET must be set when OUTPUT_WRITER=minio"
            )
        if not settings.output_minio_endpoint:
            raise ValueError(
                "OUTPUT_MINIO_ENDPOINT must be set when OUTPUT_WRITER=minio"
            )
        # D-020 R-100-118 — credentials come from dedicated runtime user
        # env vars. Standalone CLI may rely on AWS_ACCESS_KEY_ID /
        # AWS_SECRET_ACCESS_KEY (boto3 default chain) when these are empty.
        return MinioWriter(
            bucket=settings.output_minio_bucket,
            endpoint_url=settings.output_minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            region=settings.minio_region or "us-east-1",
            prefix=settings.output_minio_prefix,
        )

    if settings.output_writer in {"local", "s3"}:
        raise NotImplementedError(
            f"Output writer {settings.output_writer!r} was removed by D-020 v1 strip. "
            "Set OUTPUT_WRITER=minio."
        )

    raise ValueError(f"Unsupported output writer: {settings.output_writer!r}")
