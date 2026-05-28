# =============================================================================
# File: config.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/observability/workflow/config.py
# Description: Pydantic Settings for the workflow synthesis service.
#              Production-tier app reads this once at startup; the
#              source backend (buffer / loki / elasticsearch) is
#              selected via `OBS_SPAN_SOURCE`.
#
# @relation implements:R-100-124
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkflowSourceSettings(BaseSettings):
    """Configuration for the production-tier workflow synthesis service.

    The test-tier `_observability` collector ignores these (it always
    uses `BufferSpanSource` over its own `LogRingBuffer`); production
    apps that mount `make_workflow_router(...)` SHALL set the
    appropriate fields.
    """

    model_config = SettingsConfigDict(env_prefix="obs_", extra="ignore")

    span_source: Literal["buffer", "loki", "elasticsearch"] = Field(
        default="buffer",
        description=(
            "Which backend to query for span_summary records. "
            "'buffer' is test-tier only (ring-buffered Docker logs); "
            "'loki' and 'elasticsearch' are the two production options."
        ),
    )

    # ---- Loki -------------------------------------------------------------

    loki_url: str = Field(
        default="http://loki:3100",
        description="Base URL of the Loki HTTP API (no trailing slash).",
    )
    loki_label_selector: str = Field(
        default='{container=~"ay-.*"}',
        description=(
            "LogQL stream selector. SHOULD scope queries to the platform's "
            "containers; the adapter automatically appends a "
            "span_summary line filter."
        ),
    )

    # ---- Elasticsearch ----------------------------------------------------

    elasticsearch_url: str = Field(
        default="http://elasticsearch:9200",
        description="Base URL of the Elasticsearch HTTP API (no trailing slash).",
    )
    elasticsearch_index: str = Field(
        default="ay-logs-*",
        description=(
            "Index pattern queried by the adapter. Production deployments "
            "typically write to a date-suffixed index pattern."
        ),
    )
    elasticsearch_username: str = Field(
        default="",
        description="Optional Basic Auth username (empty disables auth).",
    )
    elasticsearch_password: str = Field(
        default="",
        description="Optional Basic Auth password (empty disables auth).",
    )

    # ---- Common -----------------------------------------------------------

    query_window_hours: float = Field(
        default=24.0,
        gt=0.0,
        le=24.0 * 30,
        description=(
            "How far back the adapter looks when fetching spans. "
            "Loki requires explicit time bounds; Elasticsearch uses this "
            "as a range filter on `@timestamp` for query performance."
        ),
    )
    request_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=120.0,
        description="HTTP timeout per upstream call.",
    )
    fetch_limit: int = Field(
        default=5_000,
        ge=1,
        le=100_000,
        description=(
            "Max number of log records pulled per query. "
            "Sets `limit=` for Loki and `size=` for Elasticsearch."
        ),
    )
