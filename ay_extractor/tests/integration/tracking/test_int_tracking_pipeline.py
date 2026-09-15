# tests/integration/tracking/test_int_tracking_pipeline.py — v2
"""Integration test for the full tracking pipeline chain.

Tests the flow: call_logger → agent_tracker → session_tracker → stats_aggregator → exporter.
No external services required — uses filesystem only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ayextractor.llm.models import LLMResponse
from ayextractor.tracking.models import LLMCallRecord, ModelPricing


def _make_response(
    input_tokens: int = 100,
    output_tokens: int = 50,
    latency_ms: int = 200,
    model: str = "qwen2.5:1.5b",
) -> LLMResponse:
    """Helper to create a mock LLMResponse."""
    return LLMResponse(
        content='{"result": "test"}',
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        provider="ollama",
        latency_ms=latency_ms,
    )


def _make_record(
    agent: str,
    step: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
    latency_ms: int = 200,
    model: str = "qwen2.5:1.5b",
    status: str = "success",
) -> LLMCallRecord:
    """Helper to create a standalone LLMCallRecord for aggregation tests."""
    import uuid
    return LLMCallRecord(
        call_id=str(uuid.uuid4()),
        timestamp=datetime.now(UTC),
        agent=agent,
        step=step,
        provider="ollama",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency_ms=latency_ms,
        status=status,
    )


class TestTrackingPipelineEndToEnd:
    """Full tracking chain integration."""

    def test_call_logger_records_and_saves(self, tmp_path: Path):
        """Level 1: CallLogger records LLMResponse objects and persists to JSONL."""
        from ayextractor.tracking.call_logger import CallLogger

        logger = CallLogger()

        # Record 3 calls via LLMResponse (as the real pipeline does)
        r1 = logger.record("summarizer", "refine_chunk_001", _make_response(100, 50, 200))
        logger.record("summarizer", "refine_chunk_002", _make_response(200, 80, 300))
        r3 = logger.record("summarizer", "extract_chunk_001", _make_response(150, 60, 250))

        assert logger.total_calls == 3
        assert logger.total_tokens == (150 + 280 + 210)

        # Verify records are LLMCallRecord instances
        assert r1.agent == "summarizer"
        assert r3.agent == "summarizer"
        assert r1.provider == "ollama"

        # Save to JSONL
        log_path = tmp_path / "calls_log.jsonl"
        logger.save(log_path)

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            data = json.loads(line)
            assert "call_id" in data
            assert "agent" in data
            assert "total_tokens" in data

    def test_agent_tracker_aggregation(self):
        """Level 2: AgentTracker aggregates per-agent stats from LLMCallRecords."""
        from ayextractor.tracking.agent_tracker import aggregate_by_agent

        records = [
            _make_record("summarizer", "s1", input_tokens=100, output_tokens=50, latency_ms=200),
            _make_record("summarizer", "s2", input_tokens=200, output_tokens=80, latency_ms=300),
            # A DISTINCT agent (real, post-D-020) — the per-agent aggregation
            # must NOT fold this into `summarizer` (its tokens are excluded from
            # the summarizer totals below ; the session totals count all three).
            _make_record("densifier", "d1", input_tokens=150, output_tokens=60, latency_ms=250),
        ]
        agent_stats = aggregate_by_agent(records)

        assert "summarizer" in agent_stats
        assert "summarizer" in agent_stats
        s = agent_stats["summarizer"]
        assert s.total_calls == 2
        assert s.total_input_tokens == 300
        assert s.total_output_tokens == 130
        assert s.total_tokens == 430
        assert s.avg_latency_ms == pytest.approx(250.0)
        assert s.max_latency_ms == 300

    def test_session_tracker_consolidation(self):
        """Level 3a: SessionTracker consolidates all records into SessionStats."""
        from ayextractor.tracking.session_tracker import build_session_stats

        records = [
            _make_record("summarizer", "s1", input_tokens=100, output_tokens=50, latency_ms=200),
            _make_record("summarizer", "s2", input_tokens=200, output_tokens=80, latency_ms=300),
            # A DISTINCT agent (real, post-D-020) — the per-agent aggregation
            # must NOT fold this into `summarizer` (its tokens are excluded from
            # the summarizer totals below ; the session totals count all three).
            _make_record("densifier", "d1", input_tokens=150, output_tokens=60, latency_ms=250),
        ]

        start = datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 16, 10, 0, 30, tzinfo=UTC)

        session = build_session_stats(
            document_id="20260216_100000_abc12345",
            records=records,
            start_time=start,
            end_time=end,
            document_size_chars=5000,
            document_size_tokens_est=1200,
        )

        assert session.document_id == "20260216_100000_abc12345"
        assert session.total_llm_calls == 3
        assert session.total_input_tokens == 450
        assert session.total_output_tokens == 190
        assert session.total_tokens == 640
        assert session.duration_seconds == pytest.approx(30.0)
        assert "summarizer" in session.agents
        assert "summarizer" in session.agents
        assert session.agents["summarizer"].total_calls == 2

    def test_stats_aggregator_cross_document(self, tmp_path: Path):
        """Level 3b: StatsAggregator accumulates across documents."""
        from ayextractor.tracking.session_tracker import build_session_stats
        from ayextractor.tracking.stats_aggregator import (
            load_global_stats,
            save_global_stats,
            update_global_stats,
        )

        # Document 1
        records_1 = [
            _make_record("summarizer", "s1", input_tokens=100, output_tokens=50),
        ]
        session_1 = build_session_stats(
            document_id="doc_001",
            records=records_1,
            start_time=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 2, 16, 10, 0, 10, tzinfo=UTC),
            document_size_chars=2000, document_size_tokens_est=500,
        )

        # Document 2
        records_2 = [
            _make_record("summarizer", "s1", input_tokens=200, output_tokens=100),
            _make_record("summarizer", "c1", input_tokens=150, output_tokens=80),
        ]
        session_2 = build_session_stats(
            document_id="doc_002",
            records=records_2,
            start_time=datetime(2026, 2, 16, 11, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 2, 16, 11, 0, 20, tzinfo=UTC),
            document_size_chars=4000, document_size_tokens_est=1000,
        )

        stats_path = tmp_path / "global_stats.json"

        # Accumulate doc 1
        stats = update_global_stats(None, session_1, records=records_1)
        assert stats.total_documents_processed == 1
        assert stats.total_tokens_consumed == 150

        save_global_stats(stats, stats_path)

        # Accumulate doc 2
        stats = load_global_stats(stats_path)
        stats = update_global_stats(stats, session_2, records=records_2)
        assert stats.total_documents_processed == 2
        assert stats.total_tokens_consumed == 150 + 530

        save_global_stats(stats, stats_path)

        # Reload and verify persistence
        reloaded = load_global_stats(stats_path)
        assert reloaded.total_documents_processed == 2

    def test_cost_calculator_integration(self):
        """CostCalculator computes costs from records + pricing dict."""
        from ayextractor.tracking.cost_calculator import compute_call_cost

        pricing = {
            "qwen2.5:1.5b": ModelPricing(
                model="qwen2.5:1.5b",
                input_price_per_1m=0.10,
                output_price_per_1m=0.30,
            ),
        }
        record = _make_record("summarizer", "s1", input_tokens=1000, output_tokens=500)
        cost = compute_call_cost(record, pricing)
        # Expected: (1000/1M * 0.10) + (500/1M * 0.30) = 0.0001 + 0.00015 = 0.00025
        assert cost == pytest.approx(0.00025)

    def test_cost_calculator_unknown_model_returns_zero(self):
        """Unknown model returns 0.0 cost."""
        from ayextractor.tracking.cost_calculator import compute_call_cost

        record = _make_record("summarizer", "s1", model="unknown-model")
        cost = compute_call_cost(record)
        assert cost == 0.0

    def test_exporter_json_and_csv(self, tmp_path: Path):
        """Exporter writes session stats as JSON + records as CSV."""
        from ayextractor.tracking.exporter import (
            export_session_csv,
            export_session_json,
            export_session_summary,
        )
        from ayextractor.tracking.session_tracker import build_session_stats

        records = [
            _make_record("summarizer", "s1"),
            _make_record("summarizer", "c1"),
        ]
        session = build_session_stats(
            document_id="doc_001",
            records=records,
            start_time=datetime(2026, 2, 16, 10, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 2, 16, 10, 0, 5, tzinfo=UTC),
            document_size_chars=1000, document_size_tokens_est=250,
        )

        # JSON export
        json_path = tmp_path / "execution_stats.json"
        export_session_json(session, json_path)
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data["document_id"] == "doc_001"

        # CSV export
        csv_path = tmp_path / "calls.csv"
        export_session_csv(records, csv_path)
        assert csv_path.exists()
        lines = csv_path.read_text().strip().split("\n")
        assert len(lines) == 3  # header + 2 records

        # Summary text
        summary = export_session_summary(session)
        assert "doc_001" in summary
        assert "summarizer" in summary
