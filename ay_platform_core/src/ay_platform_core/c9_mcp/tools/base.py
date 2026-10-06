# =============================================================================
# File: base.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/tools/base.py
# Description: Tool dataclass + dispatch helpers. Every C9 tool is a small
#              record (name, description, input schema, async handler). The
#              server maps `tools/call` to the registered handler. The
#              toolset builder wires C5 + C6 tools using the live service
#              facades (passed in via `build_default_toolset`).
#
# @relation implements:R-100-015
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from ay_platform_core.c9_mcp.models import ToolSpec


@dataclass(frozen=True)
class ActorContext:
    """The forward-auth identity of the MCP caller. Propagated to tool handlers
    via a contextvar (set by the router per request) so tools that act on the
    user's behalf — e.g. triggering a C6 validation run — can attribute the work
    for per-user/tenant quota.

    `project_scopes` is the raw `X-Project-Scopes` header value (inc3b).
    It is here because an MCP tool learns its project from a TOOL ARGUMENT,
    not from the request URI: the forwarded URI is always
    `/api/v1/mcp/...`, so C2 can derive no project role into
    `X-User-Roles`, and every downstream call to a project-scoped endpoint
    was refused with 403 for every caller. The tool resolves the role for
    the project it was asked about and forwards THAT — authority the caller
    actually proved, never a role C9 invents for itself.
    """

    user_id: str = ""
    tenant_id: str = ""
    project_scopes: str = ""


_actor: ContextVar[ActorContext | None] = ContextVar("mcp_actor", default=None)


def set_actor(user_id: str, tenant_id: str, project_scopes: str = "") -> None:
    """Record the caller identity for the current request context (router)."""
    _actor.set(
        ActorContext(
            user_id=user_id,
            tenant_id=tenant_id,
            project_scopes=project_scopes,
        )
    )


def current_actor() -> ActorContext:
    """The caller identity for the in-flight tool call (empty when unset)."""
    return _actor.get() or ActorContext()


class ToolDispatchError(RuntimeError):
    """Raised by a handler to signal a domain-side failure (4xx-equivalent).

    The server translates this into a JSON-RPC error with code
    ``ERROR_TOOL_CALL_FAILED`` and the tool-call result as ``isError=true``.
    """


Handler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    """One registered MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler = field(repr=False)

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
        )


def build_default_toolset(
    *,
    c5_service: Any,
    c6_service: Any,
) -> list[Tool]:
    """Assemble the v1 C9 toolset.

    Importing `c5_tools` and `c6_tools` here keeps this module free of a
    direct dependency on the C5/C6 service shapes, which simplifies unit
    tests that instantiate Tools directly.
    """
    from ay_platform_core.c9_mcp.tools import c5_tools, c6_tools  # noqa: PLC0415

    tools: list[Tool] = []
    tools.extend(c5_tools.build_tools(c5_service))
    tools.extend(c6_tools.build_tools(c6_service))
    return tools
