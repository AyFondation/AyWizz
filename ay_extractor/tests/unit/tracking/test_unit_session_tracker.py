# tests/unit/tracking/test_session_tracker.py — v1
"""Tests for tracking/session_tracker.py — session-level aggregation."""

from __future__ import annotations

from datetime import UTC, datetime

from ayextractor.tracking.models import LLMCallRecord
from ayextractor.tracking.session_tracker import build_session_stats


def _make_record(agent: str = "summarizer", tokens: int = 100, **kwargs) -> LLMCallRecord:
    defaults = dict(
        call_id="call_001",
        timestamp=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
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


class TestBuildSessionStats:
    def test_empty_records(self):
        stats = build_session_stats("doc_001", [])
        assert stats.document_id == "doc_001"
        assert stats.total_llm_calls == 0
        assert stats.total_tokens == 0

    def test_single_record(self):
        records = [_make_record()]
        stats = build_session_stats("doc_001", records, document_size_chars=5000)
        assert stats.total_llm_calls == 1
        assert stats.total_tokens == 150  # 100 + 50
        assert stats.total_input_tokens == 100
        assert stats.total_output_tokens == 50

    def test_multiple_agents(self):
        # Two DISTINCT agents (the original used two "summarizer" records but
        # asserted len==2 and listed "summarizer" twice — a copy-paste defect).
        records = [
            _make_record("summarizer", 100),
            _make_record("densifier", 200),
        ]
        stats = build_session_stats("doc_001", records)
        assert stats.total_llm_calls == 2
        assert len(stats.agents) == 2
        assert "summarizer" in stats.agents
        assert "densifier" in stats.agents

    def test_cost_per_1k_chars(self):
        records = [_make_record(estimated_cost_usd=0.01)]
        stats = build_session_stats("doc_001", records, document_size_chars=10000)
        assert stats.cost_per_1k_chars > 0

    def test_budget_tracking(self):
        records = [_make_record("agent_a", 500)]
        stats = build_session_stats("doc_001", records, budget_per_agent=1000)
        assert stats.budget_total_allocated == 1000
        assert stats.budget_usage_pct > 0

    def test_failed_steps(self):
        records = [
            _make_record("agent_a", status="success"),
            _make_record("agent_b", status="failed"),
        ]
        stats = build_session_stats("doc_001", records)
        assert "agent_b" in stats.steps_failed

    def test_duration_calculation(self):
        r1 = _make_record(timestamp=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC))
        r2 = _make_record(timestamp=datetime(2026, 2, 16, 10, 0, 30, tzinfo=UTC))
        stats = build_session_stats("doc_001", [r1, r2])
        assert stats.duration_seconds == 30.0
