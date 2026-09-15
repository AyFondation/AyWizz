# tests/unit/pipeline/test_state.py — v1
"""Tests for pipeline/state.py — PipelineState."""

from __future__ import annotations

from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput
from ayextractor.pipeline.state import PipelineState


class TestPipelineState:
    def test_default_creation(self):
        # D-020 v1 strip: Phase-3 state (raw_triplets, graph, …) was removed —
        # the state is now scoped to extract + chunk + optional enrichment.
        state = PipelineState()
        assert state.run_id != ""
        assert state.document_id == ""
        assert state.chunks == []
        assert state.references == []
        assert state.refine_summary == ""
        assert state.dense_summary == ""

    def test_run_id_unique(self):
        s1 = PipelineState()
        s2 = PipelineState()
        assert s1.run_id != s2.run_id

    def test_record_agent_output(self):
        state = PipelineState()
        output = AgentOutput(
            data={"key": "value"},
            confidence=0.9,
            metadata=AgentMetadata(
                agent_name="test_agent",
                agent_version="1.0.0",
                execution_time_ms=100,
                llm_calls=2,
                tokens_used=500,
            ),
        )
        state.record_agent_output("test_agent", output)

        assert "test_agent" in state.agent_outputs
        assert state.total_llm_calls == 2
        assert state.total_tokens_used == 500

    def test_record_multiple_agents(self):
        state = PipelineState()
        for i in range(3):
            output = AgentOutput(
                data={},
                confidence=0.8,
                metadata=AgentMetadata(
                    agent_name=f"agent_{i}",
                    agent_version="1.0.0",
                    execution_time_ms=50,
                    llm_calls=1,
                    tokens_used=100,
                ),
            )
            state.record_agent_output(f"agent_{i}", output)

        assert state.total_llm_calls == 3
        assert state.total_tokens_used == 300
        assert len(state.agent_outputs) == 3

    # D-020 v1 strip removed `get_graph_stats` + the `graph` field + the
    # arbitrary-type NetworkX acceptance test alongside the Phase-3 state.

    def test_errors_accumulation(self):
        state = PipelineState()
        state.errors.append("Error 1")
        state.errors.append("Error 2")
        assert len(state.errors) == 2
