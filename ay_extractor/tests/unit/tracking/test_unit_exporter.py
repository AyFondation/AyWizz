# tests/unit/tracking/test_exporter.py — v1
"""Tests for tracking/exporter.py."""

from __future__ import annotations

from datetime import datetime

import pytest

from ayextractor.tracking.exporter import (
    export_global_json,
    export_global_summary,
    export_session_csv,
    export_session_json,
    export_session_summary,
)
from ayextractor.tracking.models import (
    AgentStats,
    DailyStats,
    GlobalStats,
    LLMCallRecord,
    ModelStats,
    SessionStats,
    TypeStats,
)


@pytest.fixture
def sample_agent_stats():
    return AgentStats(
        agent="summarizer",
        total_calls=10,
        total_input_tokens=5000,
        total_output_tokens=2000,
        total_tokens=7000,
        avg_latency_ms=250.0,
        max_latency_ms=500,
        estimated_cost_usd=0.035,
    )


@pytest.fixture
def sample_session(sample_agent_stats):
    return SessionStats(
        document_id="doc_001",
        session_id="sess_abc",
        start_time=datetime(2026, 2, 16, 10, 0, 0),
        end_time=datetime(2026, 2, 16, 10, 1, 30),
        duration_seconds=90.0,
        document_size_chars=15000,
        document_size_tokens_est=3750,
        total_llm_calls=10,
        total_input_tokens=5000,
        total_output_tokens=2000,
        total_tokens=7000,
        total_cache_read_tokens=1000,
        total_cache_write_tokens=500,
        estimated_cost_usd=0.035,
        cost_per_1k_chars=0.002333,
        agents={"summarizer": sample_agent_stats},
        budget_total_allocated=50000,
        budget_total_consumed=7000,
        budget_usage_pct=14.0,
    )


@pytest.fixture
def sample_records():
    return [
        LLMCallRecord(
            call_id="call_001",
            timestamp=datetime(2026, 2, 16, 10, 0, 5),
            agent="summarizer",
            step="extract_entities",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            input_tokens=500,
            output_tokens=200,
            total_tokens=700,
            latency_ms=250,
            status="success",
            estimated_cost_usd=0.0035,
        ),
        LLMCallRecord(
            call_id="call_002",
            timestamp=datetime(2026, 2, 16, 10, 0, 10),
            agent="summarizer",
            step="extract_entities",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            input_tokens=600,
            output_tokens=180,
            total_tokens=780,
            latency_ms=300,
            status="success",
            estimated_cost_usd=0.004,
        ),
    ]


@pytest.fixture
def sample_global_stats():
    return GlobalStats(
        total_documents_processed=5,
        total_tokens_consumed=35000,
        total_estimated_cost_usd=0.175,
        avg_tokens_per_document=7000.0,
        avg_cost_per_document=0.035,
        avg_duration_per_document=90.0,
        by_document_type={
            "pdf": TypeStats(count=3, avg_tokens=8000.0, avg_cost_usd=0.04,
                             avg_duration_seconds=95.0, avg_chunks=12.0),
            "docx": TypeStats(count=2, avg_tokens=5500.0, avg_cost_usd=0.0275,
                              avg_duration_seconds=80.0, avg_chunks=8.0),
        },
        by_model={
            "claude-sonnet-4-20250514": ModelStats(
                total_calls=50,
                total_input_tokens=25000,
                total_output_tokens=10000,
                total_cost_usd=0.175,
            ),
        },
        last_updated=datetime(2026, 2, 16, 11, 0, 0),
    )


class TestExportSessionJson:
    def test_creates_file(self, tmp_path, sample_session):
        path = tmp_path / "stats" / "session.json"
        export_session_json(sample_session, path)
        assert path.exists()

    def test_valid_json(self, tmp_path, sample_session):
        import json
        path = tmp_path / "session.json"
        export_session_json(sample_session, path)
        data = json.loads(path.read_text())
        assert data["document_id"] == "doc_001"
        assert data["total_tokens"] == 7000

    def test_creates_parent_dirs(self, tmp_path, sample_session):
        path = tmp_path / "deep" / "nested" / "session.json"
        export_session_json(sample_session, path)
        assert path.exists()


class TestExportSessionCsv:
    def test_creates_file(self, tmp_path, sample_records):
        path = tmp_path / "calls.csv"
        export_session_csv(sample_records, path)
        assert path.exists()

    def test_csv_content(self, tmp_path, sample_records):
        path = tmp_path / "calls.csv"
        export_session_csv(sample_records, path)
        content = path.read_text()
        assert "call_id" in content  # Header
        assert "call_001" in content
        assert "call_002" in content
        assert "summarizer" in content

    def test_csv_row_count(self, tmp_path, sample_records):
        import csv
        path = tmp_path / "calls.csv"
        export_session_csv(sample_records, path)
        with path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2

    def test_empty_records(self, tmp_path):
        path = tmp_path / "empty.csv"
        export_session_csv([], path)
        assert path.exists()
        content = path.read_text()
        assert "call_id" in content  # Header present even with no data


class TestExportSessionSummary:
    def test_contains_document_id(self, sample_session):
        summary = export_session_summary(sample_session)
        assert "doc_001" in summary

    def test_contains_duration(self, sample_session):
        summary = export_session_summary(sample_session)
        assert "90.0s" in summary

    def test_contains_agent(self, sample_session):
        summary = export_session_summary(sample_session)
        assert "summarizer" in summary

    def test_contains_cost(self, sample_session):
        summary = export_session_summary(sample_session)
        assert "$0.035" in summary

    def test_degraded_steps(self, sample_session):
        sample_session.steps_degraded = ["summarizer"]
        summary = export_session_summary(sample_session)
        assert "summarizer" in summary
        assert "Degraded" in summary

    def test_failed_steps(self, sample_session):
        sample_session.steps_failed = ["densifier"]
        summary = export_session_summary(sample_session)
        assert "densifier" in summary
        assert "Failed" in summary


class TestExportGlobalJson:
    def test_creates_file(self, tmp_path, sample_global_stats):
        path = tmp_path / "global.json"
        export_global_json(sample_global_stats, path)
        assert path.exists()

    def test_valid_json(self, tmp_path, sample_global_stats):
        import json
        path = tmp_path / "global.json"
        export_global_json(sample_global_stats, path)
        data = json.loads(path.read_text())
        assert data["total_documents_processed"] == 5


class TestExportGlobalSummary:
    def test_contains_totals(self, sample_global_stats):
        summary = export_global_summary(sample_global_stats)
        assert "5" in summary
        assert "$0.175" in summary

    def test_contains_doc_types(self, sample_global_stats):
        summary = export_global_summary(sample_global_stats)
        assert "pdf" in summary
        assert "docx" in summary

    def test_contains_model_stats(self, sample_global_stats):
        summary = export_global_summary(sample_global_stats)
        assert "claude-sonnet-4-20250514" in summary
