# tests/unit/tracking/test_call_logger.py — v1
"""Tests for tracking/call_logger.py."""

from __future__ import annotations

from ayextractor.llm.models import LLMResponse
from ayextractor.tracking.call_logger import CallLogger


class TestCallLogger:
    def test_record(self):
        logger = CallLogger()
        resp = LLMResponse(
            content="test", input_tokens=100, output_tokens=50,
            model="claude-sonnet-4-20250514", provider="anthropic", latency_ms=500,
        )
        record = logger.record("summarizer", "refine_chunk_001", resp)
        assert record.agent == "summarizer"
        assert record.total_tokens == 150

    def test_multiple_records(self):
        logger = CallLogger()
        resp = LLMResponse(
            content="test", input_tokens=100, output_tokens=50,
            model="test", provider="test", latency_ms=100,
        )
        logger.record("summarizer", "step1", resp)
        logger.record("densifier", "step2", resp)
        assert logger.total_calls == 2
        assert logger.total_tokens == 300

    def test_save(self, tmp_path):
        logger = CallLogger()
        resp = LLMResponse(
            content="test", input_tokens=100, output_tokens=50,
            model="test", provider="test", latency_ms=100,
        )
        logger.record("test", "step", resp)
        out = tmp_path / "calls.jsonl"
        logger.save(out)
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 1
