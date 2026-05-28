# =============================================================================
# File: main.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/_observability/main.py
# Description: FastAPI app factory for the test-tier observability collector.
#              Exposes a small HTTP surface for the test harness to read
#              live + buffered logs from every `ay-*` container in the
#              compose stack.
#
#              NOT a platform component (R-100-121). Underscore prefix
#              matches `_mock_llm`. Image is the shared `ay-api:local`,
#              selected at runtime via `COMPONENT_MODULE=_observability`.
#
#              v2 (R-100-124): the `/workflows*` endpoints are now
#              served by the shared `observability.workflow` router
#              with a `BufferSpanSource`. Production K8s deployments
#              re-use the same router with `LokiSpanSource` /
#              `ElasticsearchSpanSource` — one synthesis code path,
#              three storage backends.
#
# @relation implements:R-100-120
# @relation implements:R-100-121
# @relation implements:R-100-124
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, Query
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ay_platform_core._observability.buffer import LogEntry, LogRingBuffer
from ay_platform_core._observability.collector import LogCollector
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.config import LoggingSettings
from ay_platform_core.observability.workflow import (
    BufferSpanSource,
    make_workflow_router,
)


class ObservabilityConfig(BaseSettings):
    """Runtime configuration for the observability collector."""

    model_config = SettingsConfigDict(env_prefix="obs_", extra="ignore")

    buffer_size_per_service: int = Field(
        default=5000,
        ge=100,
        description="Ring-buffer size per monitored service (lines).",
    )
    docker_socket_path: str = Field(
        default="/var/run/docker.sock",
        description="Path to the Docker daemon UNIX socket (read-only mount).",
    )
    service_filter_prefix: str = Field(
        default="ay-",
        description="Prefix every monitored container name SHALL start with.",
    )


def _entry_to_dict(e: LogEntry) -> dict[str, str]:
    return {
        "service": e.service,
        "timestamp": e.timestamp.isoformat(),
        "severity": e.severity,
        "line": e.line,
    }


def create_app(config: ObservabilityConfig | None = None) -> FastAPI:
    cfg = config or ObservabilityConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="_observability", settings=log_cfg)
    buffer = LogRingBuffer(max_per_service=cfg.buffer_size_per_service)
    collector = LogCollector(
        buffer=buffer,
        service_filter_prefix=cfg.service_filter_prefix,
        docker_socket_path=cfg.docker_socket_path,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        collector.start()
        try:
            yield
        finally:
            collector.stop()

    app = FastAPI(
        title="C_obs Observability collector (test-tier, R-100-120)",
        lifespan=lifespan,
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    # Expose the buffer on app.state so tests can pre-seed records
    # without going through the live Docker collector.
    app.state.log_buffer = buffer

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "_observability"}

    @app.get("/logs")
    async def logs(
        service: str | None = None,
        since: datetime | None = None,
        min_severity: str | None = None,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
    ) -> list[dict[str, str]]:
        entries = buffer.tail(
            service=service,
            since=since,
            min_severity=min_severity,
            limit=limit,
        )
        return [_entry_to_dict(e) for e in entries]

    @app.get("/errors")
    async def errors(
        since: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
    ) -> list[dict[str, str]]:
        entries = buffer.tail(since=since, min_severity="ERROR", limit=limit)
        return [_entry_to_dict(e) for e in entries]

    @app.get("/digest")
    async def digest() -> dict[str, dict[str, int]]:
        return buffer.digest()

    @app.get("/services")
    async def services() -> list[str]:
        return buffer.services()

    @app.post("/clear")
    async def clear() -> dict[str, str]:
        buffer.clear()
        return {"status": "cleared"}

    # Workflow synthesis (Q-100-014 / R-100-124): the test-tier collector
    # delegates to the shared production router with `BufferSpanSource`.
    # Production K8s services mount the same router with Loki / ES.
    app.include_router(make_workflow_router(BufferSpanSource(buffer)))

    return app


app = create_app()
