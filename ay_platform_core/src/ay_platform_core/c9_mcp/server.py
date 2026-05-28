# =============================================================================
# File: server.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/server.py
# Description: MCPServer facade — stateless JSON-RPC 2.0 dispatcher.
#              Exposes three methods: `initialize`, `tools/list`, `tools/call`.
#              `tools/call` routes to the registered Tool handler and wraps
#              domain errors (ToolDispatchError / HTTPException) into MCP
#              `isError=true` results.
#
# @relation implements:R-100-015
# =============================================================================

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request, status

from ay_platform_core.c9_mcp.config import MCPConfig
from ay_platform_core.c9_mcp.models import (
    ERROR_INVALID_PARAMS,
    ERROR_INVALID_REQUEST,
    ERROR_METHOD_NOT_FOUND,
    ERROR_PARSE,
    ERROR_TOOL_CALL_FAILED,
    ERROR_TOOL_NOT_FOUND,
    InitializeResult,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    ServerInfo,
    ToolCallRequest,
    ToolCallResult,
)
from ay_platform_core.c9_mcp.tools.base import Tool, ToolDispatchError


class MCPServer:
    """Stateless JSON-RPC 2.0 server implementing the MCP subset we need."""

    def __init__(self, config: MCPConfig, tools: list[Tool]) -> None:
        self._config = config
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    @property
    def config(self) -> MCPConfig:
        return self._config

    def tools(self) -> list[Tool]:
        """Return the registered tools in registration order."""
        return list(self._tools.values())

    # ------------------------------------------------------------------
    # Top-level JSON-RPC handler
    # ------------------------------------------------------------------

    async def handle_raw(self, body: bytes) -> JSONRPCResponse:
        """Parse + dispatch one request. Never raises; always returns a response."""
        if len(body) > self._config.max_tool_args_bytes:
            return _error_response(
                None,
                ERROR_INVALID_REQUEST,
                (
                    f"request body exceeds {self._config.max_tool_args_bytes} "
                    "bytes — refusing"
                ),
            )
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _error_response(None, ERROR_PARSE, f"parse error: {exc}")
        return await self._dispatch_payload(payload)

    async def handle_request(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """Dispatch an already-parsed JSONRPCRequest."""
        return await self._dispatch_request(request)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _dispatch_payload(self, payload: Any) -> JSONRPCResponse:
        try:
            req = JSONRPCRequest.model_validate(payload)
        except Exception as exc:
            req_id = payload.get("id") if isinstance(payload, dict) else None
            return _error_response(
                req_id,
                ERROR_INVALID_REQUEST,
                f"invalid JSON-RPC envelope: {exc}",
            )
        return await self._dispatch_request(req)

    async def _dispatch_request(self, req: JSONRPCRequest) -> JSONRPCResponse:
        method = req.method
        if method == "initialize":
            return self._initialize(req)
        if method == "notifications/initialized":
            # Notifications have no response per JSON-RPC 2.0, but callers
            # using our HTTP surface still expect a response. We return an
            # empty result.
            return JSONRPCResponse(id=req.id, result={})
        if method == "tools/list":
            return self._tools_list(req)
        if method == "tools/call":
            return await self._tools_call(req)
        return _error_response(
            req.id,
            ERROR_METHOD_NOT_FOUND,
            f"method not found: {method!r}",
        )

    def _initialize(self, req: JSONRPCRequest) -> JSONRPCResponse:
        result = InitializeResult(
            protocolVersion=self._config.protocol_version,
            capabilities={"tools": {"listChanged": False}},
            serverInfo=ServerInfo(
                name=self._config.server_name,
                version=self._config.server_version,
            ),
        )
        return JSONRPCResponse(
            id=req.id, result=result.model_dump(mode="json", by_alias=True)
        )

    def _tools_list(self, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(
            id=req.id,
            result={
                "tools": [
                    t.spec().model_dump(mode="json", by_alias=True)
                    for t in self._tools.values()
                ]
            },
        )

    async def _tools_call(self, req: JSONRPCRequest) -> JSONRPCResponse:
        try:
            call = ToolCallRequest.model_validate(req.params or {})
        except Exception as exc:
            return _error_response(
                req.id,
                ERROR_INVALID_PARAMS,
                f"invalid tools/call params: {exc}",
            )

        tool = self._tools.get(call.name)
        if tool is None:
            return _error_response(
                req.id,
                ERROR_TOOL_NOT_FOUND,
                f"tool not found: {call.name!r}",
            )

        try:
            raw_result = await tool.handler(call.arguments)
        except ToolDispatchError as exc:
            # Known bad input — use the MCP-standard isError result envelope
            # rather than a transport-level JSON-RPC error, so clients can
            # surface the message to the end user.
            return JSONRPCResponse(
                id=req.id,
                result=ToolCallResult(
                    content=[{"type": "text", "text": str(exc)}],
                    isError=True,
                ).model_dump(mode="json", by_alias=True),
            )
        except HTTPException as exc:
            return JSONRPCResponse(
                id=req.id,
                result=ToolCallResult(
                    content=[
                        {
                            "type": "text",
                            "text": f"HTTP {exc.status_code}: {exc.detail}",
                        }
                    ],
                    isError=True,
                ).model_dump(mode="json", by_alias=True),
            )
        except Exception as exc:
            return _error_response(
                req.id,
                ERROR_TOOL_CALL_FAILED,
                f"tool {call.name!r} failed: {type(exc).__name__}: {exc}",
            )

        # Tools return a plain dict which we serialise as the MCP result body.
        text_payload = json.dumps(raw_result, ensure_ascii=False, indent=2)
        envelope = ToolCallResult(
            content=[{"type": "text", "text": text_payload}], isError=False
        )
        return JSONRPCResponse(
            id=req.id, result=envelope.model_dump(mode="json", by_alias=True)
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(
    req_id: Any, code: int, message: str
) -> JSONRPCResponse:
    return JSONRPCResponse(
        id=req_id,
        error=JSONRPCError(code=code, message=message),
    )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_server(request: Request) -> MCPServer:
    srv = getattr(request.app.state, "mcp_server", None)
    if srv is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP server not initialised",
        )
    return srv  # type: ignore[no-any-return]
