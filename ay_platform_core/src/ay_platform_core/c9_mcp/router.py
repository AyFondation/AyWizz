# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/router.py
# Description: FastAPI APIRouter for C9. Exposes the JSON-RPC endpoint plus
#              two convenience/admin endpoints (tools listing, health).
#              Forward-auth headers (X-User-Id, X-User-Roles) propagated by
#              C1 are required on every call: MCP itself has no auth model,
#              so we enforce identity at the transport boundary.
#
# @relation implements:R-100-015
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ay_platform_core.c9_mcp.models import (
    JSONRPCResponse,
    ToolSpec,
)
from ay_platform_core.c9_mcp.server import MCPServer, get_server

router = APIRouter(tags=["mcp"])


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


@router.post(
    "/api/v1/mcp",
    response_model=JSONRPCResponse,
    response_model_exclude_none=True,
)
async def jsonrpc(
    request: Request,
    _user: str = Depends(_require_actor),
    server: MCPServer = Depends(get_server),
) -> JSONRPCResponse:
    """JSON-RPC 2.0 endpoint for MCP clients.

    Deliberately reads the raw body: MCP error semantics require the server
    to produce a well-formed JSON-RPC error response on parse failures,
    which FastAPI's default 422 behaviour would prevent.
    """
    body = await request.body()
    return await server.handle_raw(body)


@router.get(
    "/api/v1/mcp/tools",
    response_model=list[ToolSpec],
    response_model_exclude_none=True,
)
async def list_tools(
    _user: str = Depends(_require_actor),
    server: MCPServer = Depends(get_server),
) -> list[ToolSpec]:
    """Admin/debug view — not part of the MCP protocol itself."""
    return [t.spec() for t in server.tools()]


@router.get("/api/v1/mcp/health", response_model=None)
async def health(
    server: MCPServer = Depends(get_server),
) -> dict[str, str]:
    _ = server
    return {"status": "ok"}
