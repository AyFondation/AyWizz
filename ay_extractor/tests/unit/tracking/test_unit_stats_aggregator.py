# tests/unit/tracking/test_stats_aggregator.py — v1
"""Tests for tracking/stats_aggregator.py — cross-document cumulative stats."""

from __future__ import annotations

from datetime import UTC, datetime

from ayextractor.tracking.models import AgentStats, LLMCallRecord, SessionStats
from ayextractor.tracking.stats_aggregator import (
    load_global_stats,
    save_global_stats,
    update_global_stats,
)


def _make_session(
    doc_id: str = "doc_001",
    total_tokens: int = 1000,
    cost: float = 0.01,
    duration: float = 10.0,
) -> SessionStats:
    return SessionStats(
        document_id=doc_id,
        session_id="sess_001",
        start_time=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 2, 16, 10, 0, 10, tzinfo=UTC),
        duration_seconds=duration,
        document_size_chars=5000,
        document_size_tokens_est=1200,
        total_llm_calls=5,
        total_input_tokens=total_tokens // 2,
        total_output_tokens=total_tokens // 2,
        total_tokens=total_tokens,
        estimated_cost_usd=cost,
        # Two DISTINCT agents (the original had a duplicate "summarizer" dict
        # key — the second silently overwrote the first, so accumulation never
        # matched the "3+3" intent).
        agents={
            "summarizer": AgentStats(
                agent="summarizer",
                total_calls=3,
                total_input_tokens=300,
                total_output_tokens=150,
                total_tokens=450,
                avg_latency_ms=200.0,
                max_latency_ms=400,
                estimated_cost_usd=cost * 0.6,
            ),
            "densifier": AgentStats(
                agent="densifier",
                total_calls=2,
                total_input_tokens=200,
                total_output_tokens=100,
                total_tokens=300,
                avg_latency_ms=300.0,
                max_latency_ms=500,
                estimated_cost_usd=cost * 0.4,
            ),
        },
    )


def _make_record(model: str = "claude-sonnet-4-20250514") -> LLMCallRecord:
    return LLMCallRecord(
        call_id="call_001",
        timestamp=datetime(2026, 2, 16, tzinfo=UTC),
        agent="summarizer",
        step="step_1",
        provider="anthropic",
        model=model,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        latency_ms=500,
        status="success",
    )


class TestUpdateGlobalStats:
    def test_first_document(self):
        session = _make_session()
        stats = update_global_stats(None, session, document_type="pdf", num_chunks=10)
        assert stats.total_documents_processed == 1
        assert stats.total_tokens_consumed == 1000
        assert stats.avg_tokens_per_document == 1000.0
        assert "pdf" in stats.by_document_type
        assert stats.by_document_type["pdf"].count == 1

    def test_second_document(self):
        session1 = _make_session("doc_001", total_tokens=1000, cost=0.01)
        stats = update_global_stats(None, session1, document_type="pdf")
        session2 = _make_session("doc_002", total_tokens=2000, cost=0.02)
        stats = update_global_stats(stats, session2, document_type="pdf")
        assert stats.total_documents_processed == 2
        assert stats.total_tokens_consumed == 3000
        assert stats.avg_tokens_per_document == 1500.0

    def test_different_document_types(self):
        s1 = _make_session("doc_001")
        stats = update_global_stats(None, s1, document_type="pdf")
        s2 = _make_session("doc_002")
        stats = update_global_stats(stats, s2, document_type="epub")
        assert len(stats.by_document_type) == 2
        assert "pdf" in stats.by_document_type
        assert "epub" in stats.by_document_type

    def test_agent_accumulation(self):
        s1 = _make_session("doc_001")
        stats = update_global_stats(None, s1)
        s2 = _make_session("doc_002")
        stats = update_global_stats(stats, s2)
        assert stats.by_agent["summarizer"].total_calls == 6  # 3+3

    def test_model_stats_from_records(self):
        session = _make_session()
        records = [_make_record("claude-sonnet-4-20250514"), _make_record("gpt-4o")]
        stats = update_global_stats(None, session, records=records)
        assert "claude-sonnet-4-20250514" in stats.by_model
        assert "gpt-4o" in stats.by_model

    def test_cost_trend(self):
        s1 = _make_session("doc_001", cost=0.01)
        stats = update_global_stats(None, s1)
        assert len(stats.cost_trend) == 1
        assert stats.cost_trend[0].documents_processed == 1

        s2 = _make_session("doc_002", cost=0.02)
        stats = update_global_stats(stats, s2)
        # Same day, so should merge into one entry
        assert len(stats.cost_trend) == 1
        assert stats.cost_trend[0].documents_processed == 2


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        session = _make_session()
        stats = update_global_stats(None, session, document_type="pdf")
        path = tmp_path / "global_stats.json"
        save_global_stats(stats, path)
        loaded = load_global_stats(path)
        assert loaded is not None
        assert loaded.total_documents_processed == 1
        assert loaded.total_tokens_consumed == 1000

    def test_load_missing_file(self, tmp_path):
        result = load_global_stats(tmp_path / "nonexistent.json")
        assert result is None
