# tests/unit/tracking/test_agent_tracker.py — v1
"""Tests for tracking/agent_tracker.py — per-agent aggregation."""

from __future__ import annotations

from datetime import UTC, datetime

from ayextractor.tracking.agent_tracker import aggregate_by_agent
from ayextractor.tracking.models import LLMCallRecord


def _make_record(agent: str = "summarizer", tokens: int = 100, **kwargs) -> LLMCallRecord:
    defaults = dict(
        call_id="call_001",
        timestamp=datetime(2026, 2, 16, tzinfo=UTC),
        agent=agent,
        step="step_1",
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        input_tokens=tokens,
        output_tokens=tokens // 2,
        total_tokens=tokens + tokens // 2,
        latency_ms=500,
        status="success",
    )
    defaults.update(kwargs)
    return LLMCallRecord(**defaults)


class TestAggregateByAgent:
    def test_empty(self):
        result = aggregate_by_agent([])
        assert result == {}

    def test_single_agent(self):
        records = [_make_record("summarizer", 100), _make_record("summarizer", 200)]
        result = aggregate_by_agent(records)
        assert "summarizer" in result
        assert result["summarizer"].total_calls == 2
        assert result["summarizer"].total_input_tokens == 300

    def test_multiple_agents(self):
        # Two DISTINCT agents (the original used three "summarizer" records but
        # asserted len==2 and contradictory call counts — a copy-paste defect).
        records = [
            _make_record("summarizer", 100),
            _make_record("summarizer", 200),
            _make_record("densifier", 50),
        ]
        result = aggregate_by_agent(records)
        assert len(result) == 2
        assert result["summarizer"].total_calls == 2
        assert result["densifier"].total_calls == 1

    def test_budget_usage(self):
        records = [_make_record("summarizer", 100)]
        result = aggregate_by_agent(records, budget_per_agent=300)
        assert result["summarizer"].budget_usage_pct > 0

    def test_failure_count(self):
        records = [
            _make_record("agent_a", status="success"),
            _make_record("agent_a", status="failed"),
            _make_record("agent_a", status="failed"),
        ]
        result = aggregate_by_agent(records)
        assert result["agent_a"].failure_count == 2

    def test_latency_aggregation(self):
        records = [
            _make_record("agent_a", latency_ms=100),
            _make_record("agent_a", latency_ms=300),
        ]
        result = aggregate_by_agent(records)
        assert result["agent_a"].avg_latency_ms == 200.0
        assert result["agent_a"].max_latency_ms == 300
