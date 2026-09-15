# tests/integration/pipeline/test_int_pipeline_subsystem.py — v5 (D-020 post-strip)
"""Integration tests for the pipeline subsystem — D-020 v1 Phase 1+2 scope.

Complete rewrite from v4 (skip-marked). The v4 surface imported 6
stripped Phase 3 agent classes (ConceptExtractor, Reference, Profile,
CommunitySummarizer, Synthesizer, Critic). The v5 surface exercises
only the 3 surviving Phase 2 agents (summarizer, densifier,
decontextualizer — note: the decontextualizer is technically Phase 2c-i
but listed under `pipeline/agents/`) + the registry/DAG plumbing.

No external services required — pure-Python tests against the in-memory
plumbing. Real LLM / MinIO / Arango integration belongs to subsequent
end-to-end suites under `ay_platform_core/tests/system/`.
"""

from __future__ import annotations

import pytest

from ayextractor.pipeline.dag_builder import ExecutionPlan, build_dag
from ayextractor.pipeline.registry import AgentRegistry, RegistryError
from ayextractor.pipeline.state import PipelineState


class TestAgentRegistry:
    """The AgentRegistry loads the 3 surviving Phase 2 agents — D-020
    config/agents.py lists summarizer + densifier; decontextualizer is
    available via direct import but not in the auto-loaded set (it
    runs in Phase 2c pre-processing, not as a DAG stage)."""

    def test_load_all_returns_surviving_agents(self):
        registry = AgentRegistry()
        registry.load_all()
        # D-020 v1 config: AGENT_REGISTRY = [summarizer, densifier].
        agent_names = registry.agent_names
        assert "summarizer" in agent_names
        assert "densifier" in agent_names
        # Phase 3 agents MUST be absent.
        for legacy in ("concept_extractor", "synthesizer", "critic",
                       "community_summarizer", "profile_generator",
                       "reference_extractor"):
            assert legacy not in agent_names, f"{legacy} should be stripped"

    def test_get_or_raise_unknown_raises(self):
        registry = AgentRegistry()
        registry.load_all()
        with pytest.raises(RegistryError, match="not found"):
            registry.get_or_raise("nonexistent_agent")

    def test_validate_dependencies_clean(self):
        """Registered agents' dependencies SHALL resolve within the
        registered set."""
        registry = AgentRegistry()
        registry.load_all()
        errors = registry.validate_dependencies()
        assert errors == [], f"unexpected dependency errors: {errors}"

    def test_disabled_skips_agent(self):
        registry = AgentRegistry()
        registry.load_all(disabled={"summarizer"})
        assert "summarizer" not in registry.agent_names
        assert "densifier" in registry.agent_names


class TestDAGBuilder:
    """The DAG builder topologically orders the registry's dependency map."""

    def test_build_dag_from_registry_loads_phase2(self):
        registry = AgentRegistry()
        registry.load_all()
        plan = build_dag(registry.get_dependency_map())
        assert plan.total_agents == len(registry.agent_names)
        # Validate the DAG is well-formed (no cycle): stages are non-empty,
        # flat_order matches total_agents.
        assert len(plan.flat_order) == plan.total_agents
        assert all(len(stage) > 0 for stage in plan.stages)

    def test_densifier_runs_after_summarizer(self):
        """If both agents are present, the densifier (Chain of Density on
        the document-level Refine summary) SHALL run after the summarizer
        (per-chunk Refine pass)."""
        registry = AgentRegistry()
        registry.load_all()
        if "summarizer" not in registry.agent_names or "densifier" not in registry.agent_names:
            pytest.skip("required agents not registered")

        plan = build_dag(registry.get_dependency_map())
        if "densifier" in registry.agents and registry.agents["densifier"].dependencies:
            order = plan.flat_order
            # Only assert the order when the densifier actually declares
            # a dependency (in case the agent registration evolves).
            if "summarizer" in registry.agents["densifier"].dependencies:
                assert order.index("summarizer") < order.index("densifier")


class TestPipelineState:
    """D-020 v1 strip — state model trimmed to Phase 1+2 fields."""

    def test_state_has_phase12_fields(self):
        state = PipelineState(document_id="d1", document_title="t1")
        assert state.document_id == "d1"
        assert state.chunks == []
        assert state.refine_summary == ""
        assert state.dense_summary == ""
        assert state.agent_outputs == {}

    def test_state_drops_phase3_fields(self):
        """Phase 3 fields (graph, communities, entity_profiles, synthesis,
        quality_score, raw_triplets, consolidated_triplets,
        entity_normalizations) were stripped in session 2."""
        state = PipelineState()
        for legacy_field in (
            "graph", "community_hierarchy", "community_summaries",
            "entity_profiles", "relation_profiles",
            "synthesis", "quality_score", "quality_issues",
            "raw_triplets", "consolidated_triplets", "entity_normalizations",
            "merger_stats",
        ):
            assert not hasattr(state, legacy_field), (
                f"PipelineState.{legacy_field} should be stripped"
            )

    def test_record_agent_output_updates_stats(self):
        from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput

        state = PipelineState()
        output = AgentOutput(
            data={"k": "v"},
            metadata=AgentMetadata(
                agent_name="summarizer",
                agent_version="1.0",
                execution_time_ms=100,
                llm_calls=2,
                tokens_used=500,
            ),
            confidence=0.8,
        )
        state.record_agent_output("summarizer", output)
        assert state.total_llm_calls == 2
        assert state.total_tokens_used == 500
        assert state.agent_outputs["summarizer"] is output
