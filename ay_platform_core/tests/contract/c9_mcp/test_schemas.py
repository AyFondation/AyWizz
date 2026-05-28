# =============================================================================
# File: test_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c9_mcp/test_schemas.py
# Description: Contract tests for C9 — public schema validity, endpoint
#              roster, registry registration, default tool roster matches
#              the specification ratified on 2026-04-23.
# =============================================================================

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from ay_platform_core.c9_mcp.models import (
    JSONRPCRequest,
    JSONRPCResponse,
    ToolCallRequest,
    ToolCallResult,
    ToolSpec,
)
from ay_platform_core.c9_mcp.router import router
from ay_platform_core.c9_mcp.tools.base import build_default_toolset
from tests.fixtures.contract_registry import find_by_producer


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _routes() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for route in _app().routes:
        if isinstance(route, APIRoute):
            result.setdefault(route.path, set()).update(
                m.upper() for m in (route.methods or set())
            )
    return result


@pytest.mark.contract
class TestPublicSchemas:
    def test_all_are_pydantic(self) -> None:
        for model in (
            JSONRPCRequest,
            JSONRPCResponse,
            ToolSpec,
            ToolCallRequest,
            ToolCallResult,
        ):
            assert issubclass(model, BaseModel)


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {"JSONRPCRequest", "JSONRPCResponse", "ToolSpec"}

    def test_all_expected_contracts_registered(self) -> None:
        names = {c.name for c in find_by_producer("C9_mcp")}
        missing = self.EXPECTED - names
        assert not missing, f"Missing C9 contracts: {missing}"

    def test_no_unexpected_contracts(self) -> None:
        names = {c.name for c in find_by_producer("C9_mcp")}
        extra = names - self.EXPECTED
        assert not extra, f"Unexpected C9 contracts: {extra}"

    def test_all_have_consumers(self) -> None:
        for contract in find_by_producer("C9_mcp"):
            assert contract.consumers, f"{contract.name} has no declared consumers"


@pytest.mark.contract
class TestEndpointRoster:
    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("POST", "/api/v1/mcp"),
        ("GET", "/api/v1/mcp/tools"),
        ("GET", "/api/v1/mcp/health"),
    ]

    def test_every_endpoint_present(self) -> None:
        routes = _routes()
        for method, path in self.EXPECTED:
            assert path in routes, f"missing {path}"
            assert method in routes[path], (
                f"missing {method} on {path}, got {routes[path]}"
            )


@pytest.mark.contract
class TestDefaultToolset:
    """The tool roster is part of the C9 public contract. Drift here would
    break MCP clients that have been pinned to a specific tool set; bumping
    it SHALL be a versioned change.
    """

    EXPECTED_TOOLS: ClassVar[set[str]] = {
        "c5_list_entities",
        "c5_get_entity",
        "c5_list_documents",
        "c5_get_document",
        "c5_list_relations",
        "c6_list_plugins",
        "c6_trigger_validation",
        "c6_list_findings",
    }

    def test_toolset_matches_specification(self) -> None:
        # Minimal mocks — build_default_toolset only reads attributes at
        # registration; it does not invoke anything.
        c5 = MagicMock()
        c6 = MagicMock()
        c6.list_plugins = MagicMock(return_value=[])
        c6.list_domains = MagicMock(return_value=[])
        c6.trigger_run = AsyncMock()
        c6.list_findings = AsyncMock()
        names = {t.name for t in build_default_toolset(c5_service=c5, c6_service=c6)}
        assert names == self.EXPECTED_TOOLS

    def test_every_tool_declares_input_schema(self) -> None:
        c5 = MagicMock()
        c6 = MagicMock()
        tools = build_default_toolset(c5_service=c5, c6_service=c6)
        for tool in tools:
            assert tool.input_schema.get("type") == "object", tool.name
            assert "properties" in tool.input_schema, tool.name
