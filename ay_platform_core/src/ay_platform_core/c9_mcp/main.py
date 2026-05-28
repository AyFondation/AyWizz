# =============================================================================
# File: main.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/main.py
# Description: FastAPI app factory for C9 MCP Server. In the deployed
#              container, C9 talks to C5 and C6 over the internal Docker
#              network via HTTP — it does NOT share the DB/MinIO layers
#              (R-100-015: thin wrapper, no business logic).
#
# @relation implements:R-100-015
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from ay_platform_core.c9_mcp.config import MCPConfig
from ay_platform_core.c9_mcp.remote import (
    RemoteRequirementsService,
    RemoteValidationService,
)
from ay_platform_core.c9_mcp.router import router
from ay_platform_core.c9_mcp.server import MCPServer
from ay_platform_core.c9_mcp.tools.base import build_default_toolset
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
    make_traced_client,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


class MCPRemoteSettings(BaseSettings):
    """Upstream URLs for C5 + C6 when C9 is deployed as a container."""

    model_config = SettingsConfigDict(env_prefix="c9_", extra="ignore")

    c5_base_url: str = "http://c5_requirements:8000"
    c6_base_url: str = "http://c6_validation:8000"
    http_timeout_seconds: float = 10.0


def create_app(
    config: MCPConfig | None = None,
    remote: MCPRemoteSettings | None = None,
) -> FastAPI:
    cfg = config or MCPConfig()
    rcfg = remote or MCPRemoteSettings()
    log_cfg = LoggingSettings()
    configure_logging(component="c9_mcp", settings=log_cfg)

    c5_client = make_traced_client(
        base_url=rcfg.c5_base_url,
        timeout=rcfg.http_timeout_seconds,
    )
    c6_client = make_traced_client(
        base_url=rcfg.c6_base_url,
        timeout=rcfg.http_timeout_seconds,
    )

    c5_remote = RemoteRequirementsService(rcfg.c5_base_url, c5_client)
    c6_remote = RemoteValidationService(rcfg.c6_base_url, c6_client)

    tools = build_default_toolset(c5_service=c5_remote, c6_service=c6_remote)
    server = MCPServer(cfg, tools)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await c5_client.aclose()
        await c6_client.aclose()

    app = FastAPI(title="C9 MCP Server", lifespan=lifespan)
    app.add_middleware(
        AuthGuardMiddleware,
        component="c9_mcp",
        exempt_prefixes=["/health", "/api/v1/mcp/health"],
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router)
    app.state.mcp_server = server

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c9_mcp"}

    return app


app = create_app()
