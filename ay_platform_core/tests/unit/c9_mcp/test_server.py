# =============================================================================
# File: test_server.py
# Version: 1
# Path: ay_platform_core/tests/unit/c9_mcp/test_server.py
# Description: Unit tests for MCPServer — JSON-RPC parse paths, `initialize`,
#              `tools/list`, `tools/call` dispatch + error mapping. Tools are
#              fakes so the server is exercised in isolation from C5/C6.
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException, status

from ay_platform_core.c9_mcp.config import MCPConfig
from ay_platform_core.c9_mcp.models import (
    ERROR_INVALID_PARAMS,
    ERROR_INVALID_REQUEST,
    ERROR_METHOD_NOT_FOUND,
    ERROR_PARSE,
    ERROR_TOOL_CALL_FAILED,
    ERROR_TOOL_NOT_FOUND,
    JSONRPCRequest,
)
from ay_platform_core.c9_mcp.server import MCPServer
from ay_platform_core.c9_mcp.tools.base import Tool, ToolDispatchError


def _echo_tool() -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"echoed": args}

    return Tool(
        name="echo",
        description="Echo input arguments as-is.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _failing_tool() -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        raise ToolDispatchError("bad input")

    return Tool(
        name="always_fail",
        description="Always raises ToolDispatchError.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _http_error_tool() -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="nope")

    return Tool(
        name="raises_http",
        description="Raises a FastAPI HTTPException.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _exploding_tool() -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    return Tool(
        name="explodes",
        description="Raises a generic RuntimeError (transport-level failure).",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _server(*tools: Tool) -> MCPServer:
    return MCPServer(MCPConfig(), list(tools))


@pytest.mark.unit
@pytest.mark.asyncio
class TestInitialize:
    async def test_initialize_returns_server_info(self) -> None:
        srv = _server(_echo_tool())
        req = JSONRPCRequest(id=1, method="initialize")
        resp = await srv.handle_request(req)
        assert resp.id == 1
        assert resp.error is None
        assert resp.result is not None
        assert resp.result["serverInfo"]["name"] == "ay-platform-core"
        assert resp.result["protocolVersion"] == "2025-03-26"
        assert "tools" in resp.result["capabilities"]

    async def test_notifications_initialized_returns_empty_result(self) -> None:
        srv = _server()
        req = JSONRPCRequest(id=2, method="notifications/initialized")
        resp = await srv.handle_request(req)
        assert resp.error is None
        assert resp.result == {}


@pytest.mark.unit
@pytest.mark.asyncio
class TestToolsList:
    async def test_tools_list_surfaces_registered_tools(self) -> None:
        srv = _server(_echo_tool(), _failing_tool())
        req = JSONRPCRequest(id=3, method="tools/list")
        resp = await srv.handle_request(req)
        assert resp.result is not None
        names = {t["name"] for t in resp.result["tools"]}
        assert names == {"echo", "always_fail"}


@pytest.mark.unit
@pytest.mark.asyncio
class TestToolsCall:
    async def test_tools_call_success(self) -> None:
        srv = _server(_echo_tool())
        req = JSONRPCRequest(
            id=4,
            method="tools/call",
            params={"name": "echo", "arguments": {"a": 1}},
        )
        resp = await srv.handle_request(req)
        assert resp.error is None
        assert resp.result is not None
        assert resp.result["isError"] is False
        # content[0] is a text-type entry with a JSON-serialised body
        assert resp.result["content"][0]["type"] == "text"
        assert '"echoed"' in resp.result["content"][0]["text"]

    async def test_tools_call_tool_dispatch_error_yields_is_error(self) -> None:
        srv = _server(_failing_tool())
        req = JSONRPCRequest(
            id=5,
            method="tools/call",
            params={"name": "always_fail"},
        )
        resp = await srv.handle_request(req)
        # Domain-side failure — JSON-RPC OK, envelope says isError=true.
        assert resp.error is None
        assert resp.result is not None
        assert resp.result["isError"] is True
        assert "bad input" in resp.result["content"][0]["text"]

    async def test_tools_call_http_exception_yields_is_error(self) -> None:
        srv = _server(_http_error_tool())
        req = JSONRPCRequest(
            id=6,
            method="tools/call",
            params={"name": "raises_http"},
        )
        resp = await srv.handle_request(req)
        assert resp.error is None
        assert resp.result is not None
        assert resp.result["isError"] is True
        assert "HTTP 404" in resp.result["content"][0]["text"]

    async def test_tools_call_runtime_error_yields_transport_error(self) -> None:
        srv = _server(_exploding_tool())
        req = JSONRPCRequest(
            id=7,
            method="tools/call",
            params={"name": "explodes"},
        )
        resp = await srv.handle_request(req)
        # Unexpected exception — we surface a transport-level JSON-RPC error
        # so the client knows this wasn't a domain-side "expected" failure.
        assert resp.error is not None
        assert resp.error.code == ERROR_TOOL_CALL_FAILED
        assert "kaboom" in resp.error.message

    async def test_tools_call_unknown_tool_errors(self) -> None:
        srv = _server()
        req = JSONRPCRequest(
            id=8,
            method="tools/call",
            params={"name": "ghost"},
        )
        resp = await srv.handle_request(req)
        assert resp.error is not None
        assert resp.error.code == ERROR_TOOL_NOT_FOUND

    async def test_tools_call_invalid_params_errors(self) -> None:
        srv = _server()
        req = JSONRPCRequest(
            id=9,
            method="tools/call",
            params={"wrong": "shape"},
        )
        resp = await srv.handle_request(req)
        assert resp.error is not None
        assert resp.error.code == ERROR_INVALID_PARAMS


@pytest.mark.unit
@pytest.mark.asyncio
class TestDispatcherEdges:
    async def test_unknown_method_errors(self) -> None:
        srv = _server()
        req = JSONRPCRequest(id=10, method="unknown/method")
        resp = await srv.handle_request(req)
        assert resp.error is not None
        assert resp.error.code == ERROR_METHOD_NOT_FOUND

    async def test_raw_parse_error(self) -> None:
        srv = _server()
        resp = await srv.handle_raw(b"not json")
        assert resp.error is not None
        assert resp.error.code == ERROR_PARSE

    async def test_raw_invalid_envelope(self) -> None:
        srv = _server()
        # Valid JSON, missing required `method` field.
        resp = await srv.handle_raw(b'{"jsonrpc": "2.0", "id": 1}')
        assert resp.error is not None
        assert resp.error.code == ERROR_INVALID_REQUEST
        assert resp.id == 1

    async def test_raw_empty_body_treated_as_empty_dict(self) -> None:
        srv = _server()
        resp = await srv.handle_raw(b"")
        # Empty dict does not have a `method` → invalid envelope.
        assert resp.error is not None
        assert resp.error.code == ERROR_INVALID_REQUEST

    async def test_raw_oversized_body_rejected(self) -> None:
        cfg = MCPConfig(max_tool_args_bytes=1_024)
        srv = MCPServer(cfg, [])
        body = b" " * 2_000
        resp = await srv.handle_raw(body)
        assert resp.error is not None
        assert resp.error.code == ERROR_INVALID_REQUEST
        assert "exceeds" in resp.error.message
