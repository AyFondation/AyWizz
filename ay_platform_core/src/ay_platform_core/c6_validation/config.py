# =============================================================================
# File: config.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c6_validation/config.py
# Description: Runtime settings for C6 Validation Pipeline Registry.
#
#              v2: env-var single-source refactor (R-100-110 v2, R-100-111
#              v2). Shared infra params read via validation_alias. Per-
#              component fields keep the `C6_` prefix.
#              v3 (D-017 / R-700-032): adds the opt-in T3 LLM-as-judge knobs
#              (`judge_enabled`, `judge_agent`).
#
# @relation implements:R-700-050
# @relation implements:R-100-110
# @relation implements:R-100-111
# @relation implements:R-100-112
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ValidationConfig(BaseSettings):
    """C6 runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="c6_", extra="ignore", populate_by_name=True
    )

    # ---- Platform-wide (read without prefix via validation_alias) -----------
    platform_environment: Literal["development", "testing", "staging", "production"] = (
        Field(default="development", validation_alias="PLATFORM_ENVIRONMENT")
    )

    # Shared ArangoDB
    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(
        default="changeme", validation_alias="ARANGO_PASSWORD"
    )

    # Shared MinIO
    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="ay_app", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(
        default="changeme", validation_alias="MINIO_SECRET_KEY"
    )
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")

    # ---- C6-specific (C6_ prefix) ------------------------------------------
    minio_bucket: str = "validation"

    # Per-check disable flags. Consumed by the service before dispatching a
    # check. Name pattern: a disabled check id `req-without-code` maps to
    # `C6_CHECK_REQ_WITHOUT_CODE_ENABLED=false`.
    #
    # We don't declare each flag individually; the service performs a dynamic
    # lookup via `os.environ.get`. The field below is a default for the
    # fallback path.
    default_check_enabled: bool = Field(default=True)

    # Cap on the number of findings written per run. Protects the DB when a
    # pathological run emits thousands of findings.
    max_findings_per_run: int = Field(default=5_000, ge=10)

    # ---- T3 LLM-as-judge (D-017 / R-700-032) -------------------------------
    # Opt-in : OFF by default (the judge costs provider tokens and needs a
    # reachable C8 route). When True AND the service was wired with an
    # `llm_client`, each completed run also emits a JUDGED verdict.
    judge_enabled: bool = Field(default=False)
    # C8 agent route used for the judge. SHALL resolve to a model family
    # DIFFERENT from the generator's (D-011, anti self-preference). The
    # operator configures the `c6-judge` route in the C8 agent_routes map.
    judge_agent: str = Field(default="c6-judge")
