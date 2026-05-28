# =============================================================================
# File: config.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/config.py
# Description: Runtime configuration for the C5 Requirements Service.
#
#              v2: env-var single-source refactor (R-100-110 v2, R-100-111
#              v2). Shared infra params (Arango, MinIO endpoint + creds,
#              platform environment) are read from UNPREFIXED env vars via
#              validation_alias. Only fields that legitimately differ
#              between components keep the `C5_` prefix (idempotency TTL,
#              reconcile interval, MinIO bucket).
#
# @relation implements:R-100-111
# @relation implements:R-100-110
# @relation implements:R-100-112
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RequirementsConfig(BaseSettings):
    """C5 runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="c5_", extra="ignore", populate_by_name=True
    )

    # ---- Platform-wide (read without prefix via validation_alias) -----------
    platform_environment: Literal["development", "testing", "staging", "production"] = (
        Field(default="development", validation_alias="PLATFORM_ENVIRONMENT")
    )

    # Shared ArangoDB connection
    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(
        default="changeme", validation_alias="ARANGO_PASSWORD"
    )

    # Shared MinIO connection
    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="ay_app", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(
        default="changeme", validation_alias="MINIO_SECRET_KEY"
    )
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")

    # ---- C5-specific (C5_ prefix) ------------------------------------------
    # Bucket naming follows R-300-010: projects/<pid>/requirements/ and
    # platform/requirements/. The bucket itself is a single shared bucket;
    # paths are scoped inside it.
    minio_bucket: str = "requirements"

    # Idempotency cache (R-300-021): 24 h default
    idempotency_ttl_seconds: int = 86400

    # Reconciliation worker (R-300-063): 15-minute default cadence
    reconcile_interval_seconds: int = 900
