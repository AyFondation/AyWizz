# tests/unit/pipeline/agents/test_densifier.py — v1
"""Tests for pipeline/agents/densifier.py."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, call

from ayextractor.llm.models import LLMResponse
from ayextractor.pipeline.agents.densifier import (
    DEFAULT_NUM_ITERATIONS,
    DensifierAgent,
    DensifierInput,
)
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput


# --- Fixtures ---

@pytest.fixture
def agent():
    return DensifierAgent(num_iterations=3)


@pytest.fixture
def densifier_input():
    return DensifierInput(
        refine_summary=(
            "The EU cybersecurity landscape is evolving. The NIS2 Directive "
            "broadens the scope of regulated entities. Member states must "
            "transpose it by October 2024. The directive introduces stricter "
            "penalties and supply chain security requirements."
        ),
        document_title="EU Cybersecurity Report 2025",
        language="en",
        num_iterations=3,
    )


def _make_iteration_response(iteration: int, summary: str, density: float) -> LLMResponse:
    return LLMResponse(
        content=json.dumps({
            "dense_summary": summary,
            "added_entities": [f"entity_{iteration}"],
            "removed_details": [f"detail_{iteration}"],
            "density_score": density,
        }),
        input_tokens=400, output_tokens=200,
        model="claude-sonnet-4-20250514", provider="anthropic", latency_ms=500,
    )


# --- Properties ---

class TestDensifierProperties:
    def test_name(self, agent):
        assert agent.name == "densifier"

    def test_version(self, agent):
        assert agent.version == "1.0.0"

    def test_description_mentions_density(self, agent):
        assert "density" in agent.description.lower() or "dense" in agent.description.lower()

    def test_dependencies(self, agent):
        assert "summarizer" in agent.dependencies

    def test_input_schema(self, agent):
        assert agent.input_schema is DensifierInput

    def test_prompt_file_exists(self, agent):
        from pathlib import Path
        assert agent.prompt_file is not None
        assert Path(agent.prompt_file).exists()

    def test_default_iterations(self):
        agent = DensifierAgent()
        assert agent._num_iterations == DEFAULT_NUM_ITERATIONS

    def test_custom_iterations(self):
        agent = DensifierAgent(num_iterations=7)
        assert agent._num_iterations == 7


# --- Execution ---

class TestDensifierExecution:
    @pytest.mark.asyncio
    async def test_iterates_correct_number_of_times(self, agent, densifier_input):
        llm = AsyncMock()
        responses = [
            _make_iteration_response(1, "Dense v1", 0.6),
            _make_iteration_response(2, "Dense v2", 0.75),
            _make_iteration_response(3, "Dense v3 final", 0.88),
        ]
        llm.complete = AsyncMock(side_effect=responses)

        output = await agent.execute(densifier_input, llm)

        assert llm.complete.call_count == 3
        assert output.metadata.llm_calls == 3

    @pytest.mark.asyncio
    async def test_final_summary_is_last_iteration(self, agent, densifier_input):
        llm = AsyncMock()
        responses = [
            _make_iteration_response(1, "Dense v1", 0.6),
            _make_iteration_response(2, "Dense v2", 0.75),
            _make_iteration_response(3, "Final dense summary.", 0.88),
        ]
        llm.complete = AsyncMock(side_effect=responses)

        output = await agent.execute(densifier_input, llm)

        assert output.data["dense_summary"] == "Final dense summary."
        assert output.data["final_density_score"] == pytest.approx(0.88)

    @pytest.mark.asyncio
    async def test_iterations_recorded(self, agent, densifier_input):
        llm = AsyncMock()
        responses = [
            _make_iteration_response(1, "v1", 0.5),
            _make_iteration_response(2, "v2", 0.7),
            _make_iteration_response(3, "v3", 0.85),
        ]
        llm.complete = AsyncMock(side_effect=responses)

        output = await agent.execute(densifier_input, llm)

        iterations = output.data["iterations"]
        assert len(iterations) == 3
        assert iterations[0]["iteration"] == 1
        assert iterations[1]["iteration"] == 2
        assert iterations[2]["iteration"] == 3
        # Density should improve across iterations
        assert iterations[2]["density_score"] > iterations[0]["density_score"]

    @pytest.mark.asyncio
    async def test_total_tokens_accumulated(self, agent, densifier_input):
        llm = AsyncMock()
        responses = [
            _make_iteration_response(i, f"v{i}", 0.5 + i * 0.1)
            for i in range(1, 4)
        ]
        llm.complete = AsyncMock(side_effect=responses)

        output = await agent.execute(densifier_input, llm)

        # Each response: 400 input + 200 output = 600 per iteration
        assert output.metadata.tokens_used == 3 * 600

    @pytest.mark.asyncio
    async def test_single_iteration_failure_continues(self, agent, densifier_input):
        """If one iteration fails JSON parsing, it keeps the previous summary."""
        llm = AsyncMock()
        responses = [
            _make_iteration_response(1, "Dense v1", 0.6),
            LLMResponse(
                content="Not JSON!",
                input_tokens=400, output_tokens=50,
                model="test", provider="mock", latency_ms=100,
            ),
            _make_iteration_response(3, "Dense v3 recovered", 0.8),
        ]
        llm.complete = AsyncMock(side_effect=responses)

        output = await agent.execute(densifier_input, llm)

        # Should recover: iteration 2 keeps "Dense v1", iteration 3 produces final
        assert output.data["dense_summary"] == "Dense v3 recovered"
        assert len(output.data["iterations"]) == 3
        # Iteration 2 should have a parse_error recorded
        assert "parse_error" in output.data["iterations"][1]

    @pytest.mark.asyncio
    async def test_dict_state_input(self, agent, densifier_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=_make_iteration_response(1, "ok", 0.7))
        # Need 3 responses for 3 iterations
        llm.complete = AsyncMock(side_effect=[
            _make_iteration_response(i, f"v{i}", 0.5 + i * 0.1)
            for i in range(1, 4)
        ])

        output = await agent.execute(densifier_input.model_dump(), llm)
        assert "dense_summary" in output.data

    @pytest.mark.asyncio
    async def test_invalid_state_type(self, agent):
        with pytest.raises(TypeError, match="Expected DensifierInput"):
            await agent.execute([], AsyncMock())

    @pytest.mark.asyncio
    async def test_custom_iterations_in_input(self):
        """num_iterations in input overrides agent default."""
        agent = DensifierAgent(num_iterations=5)
        inp = DensifierInput(
            refine_summary="Short summary.",
            document_title="Test", language="en",
            num_iterations=2,
        )
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=[
            _make_iteration_response(1, "v1", 0.6),
            _make_iteration_response(2, "v2", 0.8),
        ])

        output = await agent.execute(inp, llm)
        assert llm.complete.call_count == 2


# --- Validation ---

class TestDensifierValidation:
    def test_validate_good_output_improving(self, agent):
        output = AgentOutput(
            data={
                "dense_summary": "A good dense summary.",
                "iterations": [
                    {"density_score": 0.5},
                    {"density_score": 0.7},
                    {"density_score": 0.85},
                ],
                "final_density_score": 0.85,
            },
            confidence=0.85,
            metadata=AgentMetadata(
                agent_name="densifier", agent_version="1.0.0",
                execution_time_ms=1500, llm_calls=3, tokens_used=1800,
            ),
        )
        # Density improved → bonus
        score = agent.validate_output(output)
        assert score >= 0.85

    def test_validate_empty_summary(self, agent):
        output = AgentOutput(
            data={"dense_summary": "", "iterations": [{"density_score": 0.5}]},
            confidence=0.8,
            metadata=AgentMetadata(
                agent_name="densifier", agent_version="1.0.0",
                execution_time_ms=500, llm_calls=1, tokens_used=600,
            ),
        )
        assert agent.validate_output(output) == 0.0

    def test_validate_no_iterations(self, agent):
        output = AgentOutput(
            data={"dense_summary": "Something", "iterations": []},
            confidence=0.5,
            metadata=AgentMetadata(
                agent_name="densifier", agent_version="1.0.0",
                execution_time_ms=0, llm_calls=0, tokens_used=0,
            ),
        )
        assert agent.validate_output(output) == 0.0
