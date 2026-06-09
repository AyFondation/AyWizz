# =============================================================================
# File: config.py
# Version: 1
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
