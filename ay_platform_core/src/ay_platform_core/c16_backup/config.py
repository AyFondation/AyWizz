# =============================================================================
# File: config.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/config.py
# Description: Runtime settings for C16 Backup/Restore. Shared infra (Arango,
#              MinIO) read from the un-prefixed platform env vars; the dedicated
#              backups bucket is C16-specific (R-900-004).
#
# @relation implements:R-900-004
# =============================================================================

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackupConfig(BaseSettings):
    """C16 runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="c16_", extra="ignore", populate_by_name=True
    )

    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(default="changeme", validation_alias="ARANGO_PASSWORD")

    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="ay_app", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="changeme", validation_alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")

    # The dedicated, tenant-isolated backups bucket (R-900-004). C16-specific.
    backups_bucket: str = "backups"

    platform_version: str = Field(default="", validation_alias="PLATFORM_VERSION")
