# =============================================================================
# File: config.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/config.py
# Description: Runtime settings for C9. Env prefix `C9_`. v1 is stateless —
#              no DB/storage of its own — so the config mainly declares the
#              MCP server identity sent during the `initialize` handshake.
#
# @relation implements:R-100-111
# =============================================================================

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPConfig(BaseSettings):
    """C9 runtime configuration."""

    model_config = SettingsConfigDict(env_prefix="c9_", extra="ignore")

    # MCP server identity — reported in the `initialize` response per the
    # MCP spec. Clients use this to display the server in their UIs.
    server_name: str = "ay-platform-core"
    server_version: str = "1.0.0"
    protocol_version: str = "2025-03-26"

    # Hard cap on tool arguments JSON size (bytes). Defensive bound against
    # pathological payloads from external LLM agents.
    max_tool_args_bytes: int = Field(default=256_000, ge=1_024)
