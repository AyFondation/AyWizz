# =============================================================================
# File: test_tools_validation.py
# Version: 1
# Path: ay_platform_core/tests/unit/c9_mcp/test_tools_validation.py
# Description: Unit tests for argument validation in the C5/C6 tool adapters.
#              Verifies that malformed arguments raise ToolDispatchError
#              (surfacing as isError=true at the protocol boundary).
# =============================================================================

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ay_platform_core.c9_mcp.tools import c5_tools, c6_tools
from ay_platform_core.c9_mcp.tools.base import ToolDispatchError


def _c5_mock() -> MagicMock:
    c5 = MagicMock()
    c5.list_entities = AsyncMock(return_value=([], None))
    c5.get_entity = AsyncMock()
    c5.list_documents = AsyncMock(return_value=([], None))
    c5.get_document = AsyncMock()
    c5.list_relations = AsyncMock(return_value=[])
    return c5


def _c6_mock() -> MagicMock:
    c6 = MagicMock()
    c6.list_plugins = MagicMock(return_value=[])
    c6.list_domains = MagicMock(return_value=[])
    c6.trigger_run = AsyncMock()
    c6.list_findings = AsyncMock()
    return c6


@pytest.mark.unit
@pytest.mark.asyncio
class TestC5ToolValidation:
    async def test_list_entities_missing_project_id(self) -> None:
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_list_entities"
        )
        with pytest.raises(ToolDispatchError, match="project_id"):
            await tool.handler({})

    async def test_list_entities_invalid_status(self) -> None:
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_list_entities"
        )
        with pytest.raises(ToolDispatchError, match="status"):
            await tool.handler({"project_id": "p", "status": "hallucinated"})

    async def test_list_entities_invalid_limit_type(self) -> None:
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_list_entities"
        )
        with pytest.raises(ToolDispatchError, match="integer"):
            await tool.handler({"project_id": "p", "limit": "abc"})

    async def test_list_entities_limit_bounds(self) -> None:
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_list_entities"
        )
        with pytest.raises(ToolDispatchError, match=r"\[1, 500\]"):
            await tool.handler({"project_id": "p", "limit": 0})

    async def test_list_entities_boolean_rejected_as_integer(self) -> None:
        """bool is an int in Python; guard explicitly against True/False."""
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_list_entities"
        )
        with pytest.raises(ToolDispatchError, match="integer"):
            await tool.handler({"project_id": "p", "limit": True})

    async def test_get_entity_requires_entity_id(self) -> None:
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_get_entity"
        )
        with pytest.raises(ToolDispatchError, match="entity_id"):
            await tool.handler({"project_id": "p"})

    async def test_list_relations_invalid_type(self) -> None:
        tool = next(
            t for t in c5_tools.build_tools(_c5_mock()) if t.name == "c5_list_relations"
        )
        with pytest.raises(ToolDispatchError, match="relation type"):
            await tool.handler(
                {"project_id": "p", "source_id": "R-100-001", "type": "nope"}
            )


@pytest.mark.unit
@pytest.mark.asyncio
class TestC6ToolValidation:
    async def test_trigger_requires_domain(self) -> None:
        tool = next(
            t for t in c6_tools.build_tools(_c6_mock())
            if t.name == "c6_trigger_validation"
        )
        with pytest.raises(ToolDispatchError, match="domain"):
            await tool.handler({"project_id": "p"})

    async def test_trigger_rejects_non_list_check_ids(self) -> None:
        tool = next(
            t for t in c6_tools.build_tools(_c6_mock())
            if t.name == "c6_trigger_validation"
        )
        with pytest.raises(ToolDispatchError, match="check_ids"):
            await tool.handler({"domain": "code", "project_id": "p", "check_ids": "x"})

    async def test_trigger_rejects_non_list_artifacts(self) -> None:
        tool = next(
            t for t in c6_tools.build_tools(_c6_mock())
            if t.name == "c6_trigger_validation"
        )
        with pytest.raises(ToolDispatchError, match="artifacts"):
            await tool.handler({"domain": "code", "project_id": "p", "artifacts": {}})

    async def test_trigger_rejects_malformed_artifact(self) -> None:
        tool = next(
            t for t in c6_tools.build_tools(_c6_mock())
            if t.name == "c6_trigger_validation"
        )
        with pytest.raises(ToolDispatchError, match="invalid artifact"):
            await tool.handler(
                {"domain": "code", "project_id": "p", "artifacts": [{"bogus": 1}]}
            )

    async def test_list_findings_requires_run_id(self) -> None:
        tool = next(
            t for t in c6_tools.build_tools(_c6_mock()) if t.name == "c6_list_findings"
        )
        with pytest.raises(ToolDispatchError, match="run_id"):
            await tool.handler({})

    async def test_list_findings_invalid_limit(self) -> None:
        tool = next(
            t for t in c6_tools.build_tools(_c6_mock()) if t.name == "c6_list_findings"
        )
        with pytest.raises(ToolDispatchError, match=r"\[1, 1000\]"):
            await tool.handler({"run_id": "r", "limit": 10_000})
