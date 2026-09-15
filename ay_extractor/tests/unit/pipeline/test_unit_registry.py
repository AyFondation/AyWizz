# tests/unit/pipeline/test_registry.py — v1
"""Tests for pipeline/registry.py — AgentRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ayextractor.pipeline.plugin_kit.base_agent import BaseAgent
from ayextractor.pipeline.plugin_kit.models import AgentOutput
from ayextractor.pipeline.registry import AgentRegistry, RegistryError, _import_agent


class FakeAgent(BaseAgent):
    """Test agent for registry tests."""

    @property
    def name(self): return "fake"
    @property
    def version(self): return "1.0.0"
    @property
    def description(self): return "Fake agent"
    @property
    def input_schema(self): return type("M", (), {})
    @property
    def output_schema(self): return type("M", (), {})
    @property
    def dependencies(self): return []
    async def execute(self, state, llm): pass  # noqa


class FakeAgentWithDeps(BaseAgent):
    @property
    def name(self): return "dependent"
    @property
    def version(self): return "1.0.0"
    @property
    def description(self): return "Agent with deps"
    @property
    def input_schema(self): return type("M", (), {})
    @property
    def output_schema(self): return type("M", (), {})
    @property
    def dependencies(self): return ["fake"]
    async def execute(self, state, llm): pass  # noqa


class TestAgentRegistry:
    def test_manual_register(self):
        reg = AgentRegistry()
        agent = FakeAgent()
        reg.register(agent)
        assert "fake" in reg.agent_names
        assert reg.get("fake") is agent

    def test_get_nonexistent_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_get_or_raise_raises(self):
        reg = AgentRegistry()
        with pytest.raises(RegistryError):
            reg.get_or_raise("nonexistent")

    def test_get_or_raise_returns(self):
        reg = AgentRegistry()
        agent = FakeAgent()
        reg.register(agent)
        assert reg.get_or_raise("fake") is agent

    def test_validate_dependencies_ok(self):
        reg = AgentRegistry()
        reg.register(FakeAgent())
        reg.register(FakeAgentWithDeps())
        errors = reg.validate_dependencies()
        assert errors == []

    def test_validate_dependencies_missing(self):
        reg = AgentRegistry()
        reg.register(FakeAgentWithDeps())  # depends on "fake" which is not registered
        errors = reg.validate_dependencies()
        assert len(errors) == 1
        assert "fake" in errors[0]

    def test_get_dependency_map(self):
        reg = AgentRegistry()
        reg.register(FakeAgent())
        reg.register(FakeAgentWithDeps())
        dep_map = reg.get_dependency_map()
        assert dep_map["fake"] == []
        assert dep_map["dependent"] == ["fake"]

    def test_load_all_from_config(self):
        """Should load agents from AGENT_REGISTRY config."""
        reg = AgentRegistry()
        reg.load_all()
        # At minimum, summarizer and densifier should be loaded
        assert len(reg.agent_names) >= 2

    def test_load_all_with_disabled(self):
        reg = AgentRegistry()
        reg.load_all(disabled={"critic"})
        assert "critic" not in reg.agent_names

    def test_overwrite_warning(self):
        reg = AgentRegistry()
        reg.register(FakeAgent())
        reg.register(FakeAgent())  # overwrite
        assert len(reg.agent_names) == 1


class TestImportAgent:
    def test_valid_import(self):
        agent = _import_agent(
            "ayextractor.pipeline.agents.summarizer.SummarizerAgent"
        )
        assert agent.name == "summarizer"

    def test_invalid_path_raises(self):
        with pytest.raises(RegistryError):
            _import_agent("no_dots")

    def test_nonexistent_module_raises(self):
        with pytest.raises(RegistryError):
            _import_agent("nonexistent.module.Class")

    def test_nonexistent_class_raises(self):
        with pytest.raises(RegistryError):
            _import_agent("ayextractor.pipeline.agents.summarizer.NonExistent")
