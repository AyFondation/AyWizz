# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/models.py
# Description: Pydantic v2 models for C9 — JSON-RPC 2.0 envelope types and
#              MCP-specific payload types (tools/list, tools/call,
#              initialize). Kept isolated so consumer components never need
#              to know about MCP/JSON-RPC internals.
#
# @relation implements:R-100-015
# =============================================================================

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 envelope
# ---------------------------------------------------------------------------


class JSONRPCRequest(BaseModel):
    """Client → server JSON-RPC 2.0 request."""

    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = None


class JSONRPCError(BaseModel):
    """JSON-RPC 2.0 error object."""

    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: dict[str, Any] | None = None


class JSONRPCResponse(BaseModel):
    """Server → client JSON-RPC 2.0 response."""

    model_config = ConfigDict(extra="forbid")

    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    result: dict[str, Any] | None = None
    error: JSONRPCError | None = None


# JSON-RPC standard error codes (subset we actively emit)
ERROR_PARSE = -32700
ERROR_INVALID_REQUEST = -32600
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603
# Application-defined band for MCP layer
ERROR_TOOL_NOT_FOUND = -32001
ERROR_TOOL_CALL_FAILED = -32002


# ---------------------------------------------------------------------------
# MCP tool surface
# ---------------------------------------------------------------------------


class ToolSpec(BaseModel):
    """MCP tool declaration — matches the wire format from tools/list."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        alias="inputSchema",
    )


class ToolCallRequest(BaseModel):
    """Shape of `params` for the `tools/call` method."""

    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """Shape of the `result` for a successful `tools/call`."""

    model_config = ConfigDict(extra="forbid")

    content: list[dict[str, Any]] = Field(default_factory=list)
    is_error: bool = Field(default=False, alias="isError")


# ---------------------------------------------------------------------------
# initialize handshake
# ---------------------------------------------------------------------------


class ServerInfo(BaseModel):
    """Server identification returned during `initialize`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str


class InitializeResult(BaseModel):
    """Response body for the `initialize` method."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(alias="protocolVersion")
    capabilities: dict[str, Any]
    server_info: ServerInfo = Field(alias="serverInfo")
