# tests/unit/pipeline/agents/test_decontextualizer.py — v2
"""Tests for pipeline/agents/decontextualizer.py."""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from ayextractor.core.models import (
    Chunk,
    ChunkDecontextualization,
    DocumentStructure,
    Reference,
    ResolvedReference,
    Section,
)
from ayextractor.llm.models import LLMResponse, Message
from ayextractor.pipeline.agents.decontextualizer import (
    DecontextualizerAgent,
    DecontextualizerInput,
)
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput


# --- Fixtures ---

@pytest.fixture
def agent():
    return DecontextualizerAgent(window_size=2)


@pytest.fixture
def chunk():
    return Chunk(
        id="chunk_003",
        position=2,
        content="He decided to restructure the team. The company expects 15% growth.",
        source_file="report.pdf",
        char_count=66,
        word_count=12,
        token_count_est=16,
        fingerprint="abc",
    )


@pytest.fixture
def preceding():
    return [
        Chunk(
            id="chunk_001", position=0,
            content="Marc Dupont is the CEO of Acme Corp.",
            source_file="report.pdf", char_count=36, word_count=8,
            token_count_est=9, fingerprint="c1",
        ),
        Chunk(
            id="chunk_002", position=1,
            content="Acme Corp reported strong Q3 results.",
            source_file="report.pdf", char_count=38, word_count=7,
            token_count_est=9, fingerprint="c2",
        ),
    ]


@pytest.fixture
def decontext_input(chunk, preceding):
    return DecontextualizerInput(
        chunk=chunk,
        refine_summary="Marc Dupont, CEO of Acme Corp, presented Q3 results.",
        preceding_chunks=preceding,
        document_title="Acme Corp Annual Report 2025",
        language="en",
        structure=DocumentStructure(
            sections=[Section(title="Introduction", level=1, start_position=0, end_position=100)],
        ),
    )


def _make_llm_response(content_dict: dict) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(content_dict),
        input_tokens=200,
        output_tokens=100,
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        latency_ms=300,
    )


# --- Properties ---

class TestDecontextualizerProperties:
    def test_name(self, agent):
        assert agent.name == "decontextualizer"

    def test_version(self, agent):
        assert agent.version == "1.0.0"

    def test_description(self, agent):
        assert "ambiguous" in agent.description.lower() or "reference" in agent.description.lower()

    def test_input_schema(self, agent):
        assert agent.input_schema is DecontextualizerInput

    def test_prompt_file_exists(self, agent):
        from pathlib import Path
        assert agent.prompt_file is not None
        assert Path(agent.prompt_file).exists()

    def test_default_window_size(self):
        agent = DecontextualizerAgent()
        assert agent._window_size == 3

    def test_custom_window_size(self):
        agent = DecontextualizerAgent(window_size=5)
        assert agent._window_size == 5


# --- Prompt building ---

class TestPromptBuilding:
    def test_format_prompt_contains_chunk(self, agent, decontext_input):
        prompt = agent._format_prompt(decontext_input)
        assert "He decided to restructure" in prompt

    def test_format_prompt_contains_summary(self, agent, decontext_input):
        prompt = agent._format_prompt(decontext_input)
        assert "Marc Dupont" in prompt

    def test_format_prompt_contains_title(self, agent, decontext_input):
        prompt = agent._format_prompt(decontext_input)
        assert "Acme Corp Annual Report" in prompt

    def test_format_prompt_contains_toc(self, agent, decontext_input):
        prompt = agent._format_prompt(decontext_input)
        assert "Introduction" in prompt

    def test_format_prompt_preceding_window(self, agent, decontext_input):
        prompt = agent._format_prompt(decontext_input)
        # Both preceding chunks should appear (window=2)
        assert "chunk_001" in prompt
        assert "chunk_002" in prompt

    def test_format_prompt_no_preceding(self, agent, chunk):
        inp = DecontextualizerInput(
            chunk=chunk, refine_summary="", preceding_chunks=[],
            document_title="Test", language="en",
        )
        prompt = agent._format_prompt(inp)
        assert "no preceding chunks" in prompt

    def test_format_prompt_no_summary(self, agent, chunk):
        inp = DecontextualizerInput(
            chunk=chunk, refine_summary="", preceding_chunks=[],
            document_title="Test", language="en",
        )
        prompt = agent._format_prompt(inp)
        assert "first chunk" in prompt

    def test_toc_not_available(self, agent, chunk):
        inp = DecontextualizerInput(
            chunk=chunk, refine_summary="", preceding_chunks=[],
            document_title="Test", language="en", structure=None,
        )
        prompt = agent._format_prompt(inp)
        assert "not available" in prompt


# --- Execution ---

class TestDecontextualizerExecution:
    @pytest.mark.asyncio
    async def test_successful_decontextualization(self, agent, decontext_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=_make_llm_response({
            "decontextualized_content": (
                "Marc Dupont (CEO of Acme Corp) decided to restructure the team. "
                "Acme Corp expects 15% growth."
            ),
            "resolved_references": [
                {
                    "original_text": "He",
                    "resolved_text": "Marc Dupont (CEO of Acme Corp)",
                    "reference_type": "pronoun",
                    "resolution_source": "preceding_chunk",
                    "position_in_chunk": 0,
                },
                {
                    "original_text": "The company",
                    "resolved_text": "Acme Corp",
                    "reference_type": "definite_article",
                    "resolution_source": "preceding_chunk",
                    "position_in_chunk": 55,
                },
            ],
            "confidence": 0.92,
        }))

        output = await agent.execute(decontext_input, llm)

        assert output.confidence == pytest.approx(0.92)
        assert "Marc Dupont" in output.data["decontextualized_content"]
        assert len(output.data["resolved_references"]) == 2
        assert output.metadata.agent_name == "decontextualizer"
        assert output.metadata.llm_calls == 1
        assert output.metadata.tokens_used == 300  # 200 + 100

    @pytest.mark.asyncio
    async def test_no_references_to_resolve(self, agent, chunk):
        llm = AsyncMock()
        content = "Marc Dupont is the CEO of Acme Corp."
        chunk.content = content
        inp = DecontextualizerInput(
            chunk=chunk, refine_summary="Some summary",
            preceding_chunks=[], document_title="Test", language="en",
        )
        llm.complete = AsyncMock(return_value=_make_llm_response({
            "decontextualized_content": content,
            "resolved_references": [],
            "confidence": 1.0,
        }))

        output = await agent.execute(inp, llm)

        assert output.confidence == 1.0
        assert output.data["decontextualized_content"] == content
        assert output.data["resolved_references"] == []

    @pytest.mark.asyncio
    async def test_json_parse_failure_fallback(self, agent, decontext_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=LLMResponse(
            content="This is not JSON at all",
            input_tokens=200, output_tokens=50,
            model="test", provider="mock", latency_ms=100,
        ))

        output = await agent.execute(decontext_input, llm)

        # Should fallback to original content
        assert output.data["decontextualized_content"] == decontext_input.chunk.content
        assert output.confidence == 0.0
        assert output.data["resolved_references"] == []

    @pytest.mark.asyncio
    async def test_dict_state_input(self, agent, decontext_input):
        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=_make_llm_response({
            "decontextualized_content": "resolved content",
            "resolved_references": [],
            "confidence": 0.8,
        }))

        output = await agent.execute(decontext_input.model_dump(), llm)
        assert output.data["decontextualized_content"] == "resolved content"

    @pytest.mark.asyncio
    async def test_invalid_state_type(self, agent):
        llm = AsyncMock()
        with pytest.raises(TypeError, match="Expected DecontextualizerInput"):
            await agent.execute("invalid", llm)

    @pytest.mark.asyncio
    async def test_markdown_fenced_json(self, agent, decontext_input):
        """LLMs sometimes wrap JSON in markdown code fences."""
        llm = AsyncMock()
        json_content = json.dumps({
            "decontextualized_content": "resolved text",
            "resolved_references": [],
            "confidence": 0.9,
        })
        llm.complete = AsyncMock(return_value=LLMResponse(
            content=f"```json\n{json_content}\n```",
            input_tokens=100, output_tokens=50,
            model="test", provider="mock", latency_ms=100,
        ))

        output = await agent.execute(decontext_input, llm)
        assert output.data["decontextualized_content"] == "resolved text"


# --- apply_to_chunk ---

class TestApplyToChunk:
    def test_apply_preserves_original(self, agent, chunk):
        original = chunk.content
        output = AgentOutput(
            data={
                "decontextualized_content": "Resolved content here.",
                "resolved_references": [{
                    "original_text": "He",
                    "resolved_text": "Marc Dupont",
                    "reference_type": "pronoun",
                    "resolution_source": "preceding_chunk",
                    "position_in_chunk": 0,
                }],
                "confidence": 0.9,
            },
            confidence=0.9,
            metadata=AgentMetadata(
                agent_name="decontextualizer", agent_version="1.0.0",
                execution_time_ms=100, llm_calls=1, tokens_used=300,
            ),
        )

        updated = agent.apply_to_chunk(chunk, output)

        assert updated.original_content == original
        assert updated.content == "Resolved content here."
        assert updated.decontextualization is not None
        assert updated.decontextualization.applied is True
        assert len(updated.decontextualization.resolved_references) == 1
        assert updated.decontextualization.confidence == 0.9

    def test_apply_empty_references(self, agent, chunk):
        output = AgentOutput(
            data={
                "decontextualized_content": chunk.content,
                "resolved_references": [],
                "confidence": 1.0,
            },
            confidence=1.0,
            metadata=AgentMetadata(
                agent_name="decontextualizer", agent_version="1.0.0",
                execution_time_ms=50, llm_calls=1, tokens_used=150,
            ),
        )

        updated = agent.apply_to_chunk(chunk, output)
        assert updated.decontextualization.resolved_references == []


# --- Validation ---

class TestDecontextualizerValidation:
    def test_validate_good_output(self, agent):
        output = AgentOutput(
            data={"decontextualized_content": "Some resolved content"},
            confidence=0.85,
            metadata=AgentMetadata(
                agent_name="decontextualizer", agent_version="1.0.0",
                execution_time_ms=100, llm_calls=1, tokens_used=300,
            ),
        )
        assert agent.validate_output(output) == 0.85

    def test_validate_empty_content(self, agent):
        output = AgentOutput(
            data={"decontextualized_content": "  "},
            confidence=0.9,
            metadata=AgentMetadata(
                agent_name="decontextualizer", agent_version="1.0.0",
                execution_time_ms=100, llm_calls=1, tokens_used=300,
            ),
        )
        assert agent.validate_output(output) == 0.0
