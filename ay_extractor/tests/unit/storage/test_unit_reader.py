# tests/unit/storage/test_reader.py — v1
"""Tests for storage/reader.py — manifest and chunk loading."""

from __future__ import annotations

import json

import pytest

from ayextractor.storage.layout import chunks_dir, ensure_run_directories, run_manifest_path
from ayextractor.storage.reader import load_manifest


class TestLoadManifest:
    def test_load_valid(self, tmp_path):
        run_path = tmp_path / "doc" / "run1"
        ensure_run_directories(run_path)
        manifest = {
            "run_id": "run1", "document_id": "doc1",
            "pipeline_version": "0.1.0",
            "created_at": "2026-02-07T14:00:00Z", "status": "completed",
            "config_overrides_applied": {}, "llm_assignments": {},
            "prompt_hashes": {}, "steps": {},
        }
        run_manifest_path(run_path).write_text(json.dumps(manifest))
        loaded = load_manifest(run_path)
        assert loaded.run_id == "run1"

    def test_load_missing_raises(self, tmp_path):
        with pytest.raises((FileNotFoundError, Exception)):
            load_manifest(tmp_path / "nonexistent")
