# tests/unit/pipeline/agents/test_summarizer.py — v2
"""Tests for pipeline/agents/summarizer.py (map-reduce summarizer)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from ayextractor.llm.models import LLMResponse
from ayextractor.pipeline.agents.summarizer import (
    SummarizerAgent,
    SummarizerInput,
)
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput

# --- Fixtures ---


@pytest.fixture
def agent():
    return SummarizerAgent()


@pytest.fixture
def summarizer_input():
    return SummarizerInput(
        text="The NIS2 Directive applies to essential and important entities across the EU.",
        document_title="EU Cybersecurity Report",
        language="en",
    )


def _make_llm_response(content_dict: dict) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(content_dict),
        input_tokens=300, output_tokens=150,
        model="claude-haiku-4-5", provider="anthropic", latency_ms=400,
    )


# --- Properties ---


class TestSummarizerProperties:
    def test_name(self, agent):
        assert agent.name == "summarizer"

    def test_description_mentions_map_reduce(self, agent):
        assert "map-reduce" in agent.description.lower()

    def test_input_schema(self, agent):
        assert agent.input_schema is SummarizerInput

    def test_prompt_file_exists(self, agent):
        from pathlib import Path
        assert agent.prompt_file is not None
        assert Path(agent.prompt_file).exists()


# --- Prompt building ---


class TestSummarizerPrompt:
    def test_prompt_contains_text(self, agent, summarizer_input):
        prompt = agent._format_prompt(summarizer_input)
        assert "NIS2 Directive" in prompt

    def test_prompt_contains_title(self, agent, summarizer_input):
        prompt = agent._format_prompt(summarizer_input)
        assert "EU Cybersecurity Report" in prompt


# --- Execution ---


class TestSummarizerExecution:
    @pytest.mark.asyncio
    async def test_successful_summary_map(self, agent, summarizer_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=_make_llm_response({
            "summary": "The NIS2 Directive targets essential and important EU entities.",
            "confidence": 0.88,
        }))

        output = await agent.execute(summarizer_input, llm)

        assert output.confidence == pytest.approx(0.88)
        assert "NIS2" in output.data["summary"]
        assert output.metadata.agent_name == "summarizer"
        assert output.metadata.llm_calls == 1

    @pytest.mark.asyncio
    async def test_reduce_merges_partials(self, agent):
        # REDUCE: the same agent summarises joined partial summaries.
        inp = SummarizerInput(
            text="Summary A about scope.\n\n---\n\nSummary B about penalties.",
            document_title="Doc", language="en",
        )
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=_make_llm_response({
            "summary": "Unified summary covering scope and penalties.",
            "confidence": 0.9,
        }))

        output = await agent.execute(inp, llm)
        assert "scope and penalties" in output.data["summary"]

    @pytest.mark.asyncio
    async def test_json_parse_failure_falls_back_to_truncated_text(self, agent, summarizer_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=LLMResponse(
            content="Broken response",
            input_tokens=100, output_tokens=30,
            model="test", provider="mock", latency_ms=50,
        ))

        output = await agent.execute(summarizer_input, llm)

        # Fallback: a truncated copy of the input text keeps the run going.
        assert output.data["summary"] == summarizer_input.text[:500]
        assert output.confidence == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_dict_state_input(self, agent, summarizer_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=_make_llm_response({
            "summary": "Updated text",
            "confidence": 0.9,
        }))

        output = await agent.execute(summarizer_input.model_dump(), llm)
        assert output.data["summary"] == "Updated text"

    @pytest.mark.asyncio
    async def test_invalid_state_type(self, agent):
        with pytest.raises(TypeError, match="Expected SummarizerInput"):
            await agent.execute(42, AsyncMock())


# --- Validation ---


class TestSummarizerValidation:
    def test_validate_good_output(self, agent):
        output = AgentOutput(
            data={"summary": "A reasonable summary of moderate length with details."},
            confidence=0.85,
            metadata=AgentMetadata(
                agent_name="summarizer", agent_version="1.0.0",
                execution_time_ms=100, llm_calls=1, tokens_used=450,
            ),
        )
        assert agent.validate_output(output) == 0.85

    def test_validate_empty_summary(self, agent):
        output = AgentOutput(
            data={"summary": ""},
            confidence=0.9,
            metadata=AgentMetadata(
                agent_name="summarizer", agent_version="1.0.0",
                execution_time_ms=100, llm_calls=1, tokens_used=450,
            ),
        )
        assert agent.validate_output(output) == 0.0

    def test_validate_short_summary_penalized(self, agent):
        output = AgentOutput(
            data={"summary": "Too short."},
            confidence=0.9,
            metadata=AgentMetadata(
                agent_name="summarizer", agent_version="1.0.0",
                execution_time_ms=100, llm_calls=1, tokens_used=450,
            ),
        )
        assert agent.validate_output(output) <= 0.5
