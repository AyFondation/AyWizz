# =============================================================================
# File: config.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/config.py
# Description: Pydantic Settings for the shared logging / tracing helpers.
#              Read once by `configure_logging()` at component startup.
#              All three fields are platform-wide (no per-component prefix);
#              the test_env_completeness coherence test accepts them as
#              shared knobs via `validation_alias`.
#
# @relation implements:R-100-104
# @relation implements:R-100-105
# @relation implements:R-100-110
# @relation implements:R-100-111
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingSettings(BaseSettings):
    """Shared observability settings.

    Read by `configure_logging()` at component startup. NOT a per-
    component config — every component's logs and traces use the same
    values, configured once in the env file.
    """

    # populate_by_name=True so model_validate(...) accepts field names
    # alongside the validation_alias.
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
        description="Root logger level. Components SHALL emit no messages below this.",
    )
    log_format: Literal["json", "text"] = Field(
        default="json",
        validation_alias="LOG_FORMAT",
        description=(
            "Log line format. 'json' is the production default per "
            "R-100-104; 'text' is for local terminals where readability "
            "wins over machine parsing."
        ),
    )
    trace_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias="TRACE_SAMPLE_RATE",
        description=(
            "Probability that a NEW trace (no inbound `traceparent`) is "
            "sampled. Inbound traces with the sampled flag set propagate "
            "regardless. Default 1.0 (sample every trace) for local; "
            "production deployments lower this per R-100-105."
        ),
    )
