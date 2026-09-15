# tests/unit/tracking/test_cost_calculator.py — v1
"""Tests for tracking/cost_calculator.py."""

from __future__ import annotations

from datetime import datetime, timezone

from ayextractor.tracking.cost_calculator import (
    compute_agent_stats,
    compute_call_cost,
    compute_total_cost,
)
from ayextractor.tracking.models import LLMCallRecord


def _make_record(agent: str = "summarizer", model: str = "claude-sonnet-4-20250514",
                 input_tokens: int = 1000, output_tokens: int = 500) -> LLMCallRecord:
    return LLMCallRecord(
        call_id="uuid1", timestamp=datetime.now(timezone.utc),
        agent=agent, step="step1", provider="anthropic", model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency_ms=500, status="success",
    )


class TestComputeCallCost:
    def test_known_model(self):
        record = _make_record(input_tokens=1000, output_tokens=500)
        cost = compute_call_cost(record)
        assert cost > 0

    def test_unknown_model_zero(self):
        record = _make_record(model="unknown-model-xyz")
        cost = compute_call_cost(record)
        assert cost == 0.0


class TestComputeAgentStats:
    def test_single_agent(self):
        records = [_make_record() for _ in range(5)]
        stats = compute_agent_stats(records)
        assert "summarizer" in stats
        assert stats["summarizer"].total_calls == 5

    def test_multiple_agents(self):
        records = [_make_record("summarizer"), _make_record("densifier")]
        stats = compute_agent_stats(records)
        assert len(stats) == 2


class TestComputeTotalCost:
    def test_total(self):
        records = [_make_record() for _ in range(3)]
        total = compute_total_cost(records)
        assert total > 0
