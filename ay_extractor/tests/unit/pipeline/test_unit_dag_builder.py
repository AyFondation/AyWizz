# tests/unit/pipeline/test_dag_builder.py — v1
"""Tests for pipeline/dag_builder.py — DAG construction and topological sort."""

from __future__ import annotations

import pytest

from ayextractor.pipeline.dag_builder import DAGError, ExecutionPlan, build_dag


class TestBuildDAG:
    def test_empty_map(self):
        plan = build_dag({})
        assert plan.total_agents == 0
        assert plan.stages == []
        assert plan.flat_order == []

    def test_single_agent_no_deps(self):
        plan = build_dag({"summarizer": []})
        assert plan.total_agents == 1
        assert plan.flat_order == ["summarizer"]
        assert len(plan.stages) == 1

    def test_linear_chain(self):
        dep_map = {
            "a": [],
            "b": ["a"],
            "c": ["b"],
        }
        plan = build_dag(dep_map)
        assert plan.total_agents == 3
        order = plan.flat_order
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_parallel_agents_same_stage(self):
        dep_map = {
            "root": [],
            "branch_a": ["root"],
            "branch_b": ["root"],
        }
        plan = build_dag(dep_map)
        assert plan.total_agents == 3
        # root in stage 0, branches in stage 1
        assert plan.stages[0] == ["root"]
        assert set(plan.stages[1]) == {"branch_a", "branch_b"}

    def test_diamond_dependency(self):
        dep_map = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        plan = build_dag(dep_map)
        assert plan.total_agents == 4
        order = plan.flat_order
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_raises(self):
        dep_map = {
            "a": ["b"],
            "b": ["a"],
        }
        with pytest.raises(DAGError, match="Cycle"):
            build_dag(dep_map)

    def test_missing_dependency_raises(self):
        dep_map = {
            "a": ["nonexistent"],
        }
        with pytest.raises(DAGError, match="not registered"):
            build_dag(dep_map)

    def test_complex_graph(self):
        # D-020 v1 surviving Phase 1+2 agents only.
        dep_map = {
            "image_analyzer": [],
            "summarizer": [],
            "decontextualizer_screener": ["summarizer"],
            "decontextualizer": ["decontextualizer_screener"],
            "densifier": ["summarizer", "decontextualizer"],
        }
        plan = build_dag(dep_map)
        assert plan.total_agents == 5
        order = plan.flat_order
        assert order.index("summarizer") < order.index("decontextualizer")
        assert order.index("decontextualizer_screener") < order.index("decontextualizer")
        assert order.index("decontextualizer") < order.index("densifier")

    def test_stages_reflect_concurrency(self):
        dep_map = {
            "summarizer": [],
            "image_analyzer": [],
            "densifier": ["summarizer"],
            "decontextualizer": ["densifier", "image_analyzer"],
        }
        plan = build_dag(dep_map)
        # Stage 0: summarizer, image_analyzer (no deps)
        assert set(plan.stages[0]) == {"image_analyzer", "summarizer"}
        # Stage 1: densifier
        assert plan.stages[1] == ["densifier"]
        # Stage 2: decontextualizer
        assert plan.stages[2] == ["decontextualizer"]


class TestExecutionPlan:
    def test_flat_order(self):
        plan = ExecutionPlan(
            stages=[["a", "b"], ["c"], ["d"]],
            total_agents=4,
        )
        assert plan.flat_order == ["a", "b", "c", "d"]

    def test_empty_flat_order(self):
        plan = ExecutionPlan()
        assert plan.flat_order == []
