# tests/unit/storage/test_models.py — v1
"""Tests for storage/models.py — RunManifest and StepManifest."""

from __future__ import annotations

from datetime import datetime, timezone

from ayextractor.storage.models import RunManifest, StepManifest


class TestStepManifest:
    def test_fresh(self):
        s = StepManifest(origin="fresh")
        assert s.carried_from is None

    def test_carried(self):
        s = StepManifest(origin="carried_from", carried_from="run_001")
        assert s.carried_from == "run_001"


class TestRunManifest:
    def test_create(self):
        m = RunManifest(
            run_id="20260207_1615_b3c8d", document_id="doc1",
            pipeline_version="0.1.0",
            created_at=datetime.now(timezone.utc), status="running",
            config_overrides_applied={}, llm_assignments={"summarizer": "anthropic:claude-sonnet-4-20250514"},
            prompt_hashes={"summarizer": "abc123"}, steps={},
        )
        assert m.status == "running"
        assert m.completed_at is None
