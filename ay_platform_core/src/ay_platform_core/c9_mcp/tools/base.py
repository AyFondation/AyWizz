# =============================================================================
# File: base.py
# Version: 1
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
from dataclasses import dataclass, field
from typing import Any

from ay_platform_core.c9_mcp.models import ToolSpec


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
