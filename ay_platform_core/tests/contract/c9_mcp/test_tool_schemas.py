# =============================================================================
# File: test_tool_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c9_mcp/test_tool_schemas.py
# Description: Contract tests for the MCP tool input schemas. External LLM
#              clients rely on these schemas to construct valid calls — any
#              shape drift (renaming a required field, changing a type,
#              dropping a property) is a breaking change. These tests
#              pin the structural invariants for the v1 roster so drift
#              surfaces at CI time.
#
#              Validation is done structurally rather than via a full
#              JSON Schema validator to avoid adding a dependency; the
#              checks cover the subset we actually author.
# =============================================================================

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from ay_platform_core.c9_mcp.models import ToolSpec
from ay_platform_core.c9_mcp.tools.base import build_default_toolset


def _toolset() -> dict[str, ToolSpec]:
    c5 = MagicMock()
    c6 = MagicMock()
    return {t.name: t.spec() for t in build_default_toolset(c5_service=c5, c6_service=c6)}


@pytest.mark.contract
class TestSchemaStructure:
    """Every tool SHALL declare a well-formed object schema."""

    def test_every_schema_is_object_type(self) -> None:
        for name, spec in _toolset().items():
            schema = spec.input_schema
            assert schema.get("type") == "object", (
                f"{name} input_schema.type must be 'object', got {schema!r}"
            )

    def test_every_schema_has_properties_mapping(self) -> None:
        for name, spec in _toolset().items():
            props = spec.input_schema.get("properties")
            assert isinstance(props, dict), (
                f"{name} input_schema.properties must be a dict, got {props!r}"
            )

    def test_required_fields_are_declared_properties(self) -> None:
        """A field listed in ``required`` SHALL also appear in ``properties``.

        If required and properties drift apart, LLM clients produce calls
        that look valid to their local schema but fail at the server.
        """
        for name, spec in _toolset().items():
            props = spec.input_schema.get("properties", {})
            required = spec.input_schema.get("required", [])
            assert isinstance(required, list), f"{name}: required must be a list"
            missing = [r for r in required if r not in props]
            assert not missing, (
                f"{name}: required fields {missing} not in properties"
            )

    def test_every_property_declares_type(self) -> None:
        """Every declared property SHALL have a ``type`` so the LLM knows
        what primitive or container to emit. Missing types are a common
        source of runtime rejections."""
        for tool_name, spec in _toolset().items():
            props: dict[str, Any] = spec.input_schema.get("properties", {})
            for prop_name, prop_schema in props.items():
                assert "type" in prop_schema, (
                    f"{tool_name}.{prop_name}: property missing 'type' — "
                    f"got {prop_schema!r}"
                )


@pytest.mark.contract
class TestSpecifiedContracts:
    """Pin the precise required-field surface of each tool. Drift here is
    a v1 contract break; bumping it requires coordinated MCP client
    releases, so CI flags it loudly.
    """

    EXPECTED_REQUIRED: ClassVar[dict[str, set[str]]] = {
        "c5_list_entities": {"project_id"},
        "c5_get_entity": {"project_id", "entity_id"},
        "c5_list_documents": {"project_id"},
        "c5_get_document": {"project_id", "slug"},
        "c5_list_relations": {"project_id", "source_id"},
        "c6_list_plugins": set(),
        "c6_trigger_validation": {"domain", "project_id"},
        "c6_list_findings": {"run_id"},
    }

    def test_required_fields_match_specification(self) -> None:
        toolset = _toolset()
        for tool_name, expected_required in self.EXPECTED_REQUIRED.items():
            assert tool_name in toolset, f"tool {tool_name!r} missing from roster"
            actual = set(toolset[tool_name].input_schema.get("required", []))
            assert actual == expected_required, (
                f"{tool_name}: required drift. "
                f"expected={sorted(expected_required)}, "
                f"actual={sorted(actual)}"
            )


@pytest.mark.contract
class TestDescriptions:
    """LLM agents rely on descriptions to pick the right tool."""

    def test_every_tool_has_nonempty_description(self) -> None:
        for name, spec in _toolset().items():
            assert spec.description, f"{name}: description must not be empty"
            assert len(spec.description) >= 20, (
                f"{name}: description too short to be useful to an LLM: "
                f"{spec.description!r}"
            )
