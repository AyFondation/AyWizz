# tests/unit/storage/test_run_manager.py — v1
"""Tests for storage/run_manager.py — ID generation and run lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone

from ayextractor.storage.run_manager import generate_document_id, generate_run_id


class TestGenerateDocumentId:
    def test_format(self):
        doc_id = generate_document_id()
        assert isinstance(doc_id, str)
        assert len(doc_id) > 10

    def test_deterministic_with_timestamp(self):
        ts = datetime(2026, 2, 7, 14, 0, 0, tzinfo=timezone.utc)
        id1 = generate_document_id(ts)
        id2 = generate_document_id(ts)
        # IDs should start with same date prefix
        assert id1[:8] == id2[:8] == "20260207"


class TestGenerateRunId:
    def test_format(self):
        run_id = generate_run_id()
        assert isinstance(run_id, str)
        assert len(run_id) > 10
