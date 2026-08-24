# =============================================================================
# File: config.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_admin/config.py
# Description: Runtime settings for the C8 admin app. Shared infra params
#              (Arango) are read un-prefixed via validation_alias like the
#              other components; C8-admin-specific knobs keep the `C8_ADMIN_`
#              prefix. The SecretCipher master key is NOT declared here — it is
#              sourced by `SecretCipher.from_env()` directly from
#              `AY_SECRET_MASTER_KEY[S]` (Tier-2 `.env.secret`), never echoed.
# =============================================================================

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class C8AdminConfig(BaseSettings):
    """C8 admin runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="c8_admin_", extra="ignore", populate_by_name=True
    )

    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(default="changeme", validation_alias="ARANGO_PASSWORD")

    # Path to the canonical LiteLLM config whose `model_list` seeds the
    # registry on first start (empty → no seeding). Mounted read-only.
    litellm_config_path: str = ""
    # Whether to seed missing models from `litellm_config_path` on startup.
    # Idempotent; never clobbers existing rows/keys.
    seed_on_start: bool = True

    # MinIO — for the per-project storage dashboards (E-100-002 v7). When
    # `minio_endpoint` is blank the storage metering is DISABLED (the storage
    # endpoints return 503); the rest of the admin surface is unaffected. Same
    # bucket + credentials as C4's artifact store.
    minio_endpoint: str = Field(default="", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(
        default="minioadmin", validation_alias="MINIO_ACCESS_KEY"
    )
    minio_secret_key: str = Field(
        default="minioadmin", validation_alias="MINIO_SECRET_KEY"
    )
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")
    minio_bucket: str = Field(
        default="orchestrator", validation_alias="MINIO_BUCKET"
    )

    # D-011 / R-400-222 — when a registry embedding model is edited (vectors
    # change) or a project switches selection, c8_admin fires a best-effort
    # `reembed` job to this C12/n8n webhook; the workflow calls C7 /reembed as
    # the system. Blank → auto-trigger DISABLED (staleness stays visible via
    # processing_version drift; the operator reembeds manually).
    reembed_webhook_url: str = ""
    reembed_webhook_timeout_s: float = Field(default=10.0, ge=1.0)
