# tests/unit/pipeline/plugin_kit/test_models.py — v1
"""Tests for pipeline/plugin_kit/models.py — agent metadata and output."""

from __future__ import annotations

from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput


class TestAgentMetadata:
    def test_create(self):
        m = AgentMetadata(
            agent_name="summarizer", agent_version="1.0.0",
            execution_time_ms=2500, llm_calls=15, tokens_used=42000,
        )
        assert m.prompt_hash is None


class TestAgentOutput:
    def test_create(self):
        m = AgentMetadata(
            agent_name="test", agent_version="1.0.0",
            execution_time_ms=100, llm_calls=1, tokens_used=500,
        )
        o = AgentOutput(
            data={"summary": "Test"}, confidence=0.85, metadata=m,
        )
        assert o.warnings == []
        assert o.data["summary"] == "Test"
