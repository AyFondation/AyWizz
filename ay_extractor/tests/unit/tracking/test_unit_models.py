# tests/unit/tracking/test_models.py — v1
"""Tests for tracking/models.py — LLM call tracking and stats models."""

from __future__ import annotations

from datetime import datetime, timezone

from ayextractor.tracking.models import (
    AgentStats,
    CumulativeAgentStats,
    DailyStats,
    GlobalStats,
    LLMCallRecord,
    ModelPricing,
    ModelStats,
    SessionStats,
    TypeStats,
)


class TestLLMCallRecord:
    def test_create(self):
        r = LLMCallRecord(
            call_id="uuid1", timestamp=datetime.now(timezone.utc),
            agent="summarizer", step="refine_chunk_001",
            provider="anthropic", model="claude-sonnet-4-20250514",
            input_tokens=2000, output_tokens=800, total_tokens=2800,
            latency_ms=1500, status="success",
        )
        assert r.total_tokens == 2800
        assert r.cache_read_tokens == 0

    def test_retry_status(self):
        r = LLMCallRecord(
            call_id="uuid2", timestamp=datetime.now(timezone.utc),
            agent="densifier", step="pass_2", provider="openai", model="gpt-4o",
            input_tokens=100, output_tokens=50, total_tokens=150,
            latency_ms=3000, status="retry", retry_count=2,
        )
        assert r.retry_count == 2


class TestAgentStats:
    def test_create(self):
        s = AgentStats(
            agent="summarizer", total_calls=15,
            total_input_tokens=30000, total_output_tokens=12000,
            total_tokens=42000, avg_latency_ms=1200.0, max_latency_ms=3500,
        )
        assert s.budget_usage_pct == 0.0


class TestSessionStats:
    def test_create(self):
        s = SessionStats(
            document_id="doc1", session_id="sess1",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
            duration_seconds=300, document_size_chars=50000,
            document_size_tokens_est=12500, total_llm_calls=45,
            total_input_tokens=90000, total_output_tokens=35000,
            total_tokens=125000, estimated_cost_usd=1.25,
        )
        assert s.duration_seconds == 300


class TestGlobalStats:
    def test_create(self):
        g = GlobalStats(
            total_documents_processed=10, total_tokens_consumed=500000,
            total_estimated_cost_usd=5.0, avg_tokens_per_document=50000.0,
            avg_cost_per_document=0.5, avg_duration_per_document=120.0,
            last_updated=datetime.now(timezone.utc),
        )
        assert g.total_documents_processed == 10


class TestModelPricing:
    def test_create(self):
        p = ModelPricing(
            model="claude-sonnet-4-20250514",
            input_price_per_1m=3.0, output_price_per_1m=15.0,
            cache_read_per_1m=0.3, cache_write_per_1m=3.75,
        )
        assert p.cache_read_per_1m == 0.3
