# =============================================================================
# File: config.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/_sub_agent/config.py
# Description: Runtime config for the sub-agent process. Lives in its
#              own module (rather than in `runtime.py`) so the
#              coherence test that discovers `BaseSettings` subclasses
#              via the `config|main` module-name convention picks it up
#              (`tests/coherence/test_env_completeness.py` — R-100-118 v2).
#
# @relation implements:R-100-110
# @relation implements:R-100-118
# =============================================================================

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SubAgentConfig(BaseSettings):
    """Sub-agent runtime configuration sourced from pod env vars.

    Holds ONLY the sub-agent-specific fields (bundle prefix, MinIO
    creds, the bearer token used to call C8). The C8 client itself
    reads its env (`C8_GATEWAY_URL`, `C8_DEFAULT_MODEL`,
    `C8_AGENT_ROUTES_*`) via `ClientSettings()` at runtime — that
    avoids env-name collisions with the existing ClientSettings
    (R-100-110 v2 single-source rule)."""

    model_config = SettingsConfigDict(
        env_prefix="SUB_AGENT_", extra="ignore", populate_by_name=True,
    )

    # ---- MinIO (bundle storage) ------------------------------------------
    minio_endpoint: str = Field(default="", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", validation_alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")
    bundle_bucket: str = Field(default="orchestrator")
    # MinIO key prefix (no leading `/`, trailing `/`) :
    # `c4-dispatch/<run_id>/<sub_agent_id>/`.
    bundle_prefix: str = Field(default="")

    # The C8 bearer is no longer sub-agent-specific : the pod reads the
    # single shared `C8_GATEWAY_API_KEY` via ClientSettings (injected by
    # the K8s dispatcher), same credential as C3/C4.

    def validate_for_runtime(self) -> None:
        """Raise ValueError with a clear message when a required field
        is empty. Called once at startup so the pod fails fast with a
        diagnostic line rather than crashing deep inside MinIO/C8."""
        missing = [
            name
            for name, val in (
                ("MINIO_ENDPOINT", self.minio_endpoint),
                ("MINIO_ACCESS_KEY", self.minio_access_key),
                ("MINIO_SECRET_KEY", self.minio_secret_key),
                ("SUB_AGENT_BUNDLE_PREFIX", self.bundle_prefix),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                f"sub-agent runtime missing env: {', '.join(missing)}",
            )
