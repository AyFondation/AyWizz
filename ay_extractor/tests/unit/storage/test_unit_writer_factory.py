# tests/unit/storage/test_unit_writer_factory.py — v3
"""Tests for storage/writer_factory.py — D-020 session 3 (MinIO only)."""

from __future__ import annotations

import pytest

from ayextractor.config.settings import Settings
from ayextractor.storage.minio_writer import MinioWriter
from ayextractor.storage.writer_factory import create_writer


class TestCreateWriter:
    def test_default_minio(self):
        """Default config produces a MinioWriter (requires endpoint set)."""
        s = Settings(
            _env_file=None,
            output_minio_endpoint="http://localhost:9000",
            output_minio_bucket="c13-extractor-artifacts",
        )
        writer = create_writer(s)
        assert isinstance(writer, MinioWriter)

    def test_minio_missing_endpoint(self):
        s = Settings(
            _env_file=None,
            output_minio_endpoint="",
            output_minio_bucket="c13-extractor-artifacts",
        )
        with pytest.raises(ValueError, match="OUTPUT_MINIO_ENDPOINT"):
            create_writer(s)

    def test_minio_missing_bucket(self):
        s = Settings(
            _env_file=None,
            output_minio_endpoint="http://localhost:9000",
            output_minio_bucket="",
        )
        with pytest.raises(ValueError, match="OUTPUT_MINIO_BUCKET"):
            create_writer(s)

    def test_legacy_local_raises_not_implemented(self):
        """`local` writer was stripped by D-020 v1 — factory raises NotImplementedError."""
        # The `output_writer` field is `Literal["minio"]`, so the pydantic
        # validator rejects `"local"` outright. We bypass via model_construct
        # to drive the factory branch directly.
        s = Settings.model_construct(output_writer="local")
        with pytest.raises(NotImplementedError, match="D-020 v1 strip"):
            create_writer(s)

    def test_legacy_s3_raises_not_implemented(self):
        s = Settings.model_construct(output_writer="s3")
        with pytest.raises(NotImplementedError, match="D-020 v1 strip"):
            create_writer(s)
