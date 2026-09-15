# tests/unit/pipeline/plugin_kit/test_base_agent.py — v1
"""Tests for pipeline/plugin_kit/base_agent.py — BaseAgent ABC."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from ayextractor.pipeline.plugin_kit.base_agent import BaseAgent
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput


class TestBaseAgent:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseAgent()  # type: ignore[abstract]

    def test_has_required_abstract_properties(self):
        for attr in ["name", "version", "description", "input_schema", "output_schema", "execute"]:
            assert hasattr(BaseAgent, attr)

    def test_default_dependencies_is_empty(self):
        """Concrete subclass should inherit empty dependencies."""

        class DummyInput(BaseModel):
            pass

        class DummyOutput(BaseModel):
            pass

        class TestAgent(BaseAgent):
            @property
            def name(self) -> str:
                return "test"

            @property
            def version(self) -> str:
                return "1.0.0"

            @property
            def description(self) -> str:
                return "Test agent"

            @property
            def input_schema(self):
                return DummyInput

            @property
            def output_schema(self):
                return DummyOutput

            async def execute(self, state, llm):
                return AgentOutput(
                    data={},
                    confidence=1.0,
                    metadata=AgentMetadata(
                        agent_name="test", agent_version="1.0.0",
                        execution_time_ms=0, llm_calls=0, tokens_used=0,
                    ),
                )

        agent = TestAgent()
        assert agent.dependencies == []
        assert agent.prompt_file is None

    def test_default_validate_output(self):
        """Default validate_output returns 1.0."""

        class DummyInput(BaseModel):
            pass

        class DummyOutput(BaseModel):
            pass

        class TestAgent(BaseAgent):
            @property
            def name(self):
                return "t"

            @property
            def version(self):
                return "1.0.0"

            @property
            def description(self):
                return "t"

            @property
            def input_schema(self):
                return DummyInput

            @property
            def output_schema(self):
                return DummyOutput

            async def execute(self, state, llm):
                pass

        agent = TestAgent()
        dummy_output = AgentOutput(
            data={}, confidence=0.5,
            metadata=AgentMetadata(
                agent_name="t", agent_version="1.0.0",
                execution_time_ms=0, llm_calls=0, tokens_used=0,
            ),
        )
        assert agent.validate_output(dummy_output) == 1.0
