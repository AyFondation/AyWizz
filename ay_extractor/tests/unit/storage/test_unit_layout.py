# tests/unit/storage/test_layout.py — v1
"""Tests for storage/layout.py — directory path functions."""

from __future__ import annotations

from pathlib import Path

from ayextractor.storage.layout import (
    chunks_dir,
    document_root,
    ensure_run_directories,
    extraction_dir,
    metadata_dir,
    run_dir,
    run_manifest_path,
    runs_dir,
    source_dir,
    synthesis_dir,
)


class TestLayout:
    def test_document_root(self):
        p = document_root(Path("/out"), "doc1")
        assert str(p) == "/out/doc1"

    def test_source_dir(self):
        p = source_dir(Path("/out"), "doc1")
        assert "source" in str(p).lower() or "00_source" in str(p)

    def test_runs_dir(self):
        p = runs_dir(Path("/out"), "doc1")
        assert "run" in str(p).lower()

    def test_run_dir(self):
        p = run_dir(Path("/out"), "doc1", "run1")
        assert "run1" in str(p)

    def test_subdirs(self):
        rp = Path("/out/doc/run1")
        assert metadata_dir(rp).parent == rp
        assert extraction_dir(rp).parent == rp
        assert chunks_dir(rp).parent == rp
        assert synthesis_dir(rp).parent == rp

    def test_run_manifest_path(self):
        rp = Path("/out/doc/run1")
        assert "manifest" in str(run_manifest_path(rp)).lower()

    def test_ensure_run_directories(self, tmp_path):
        rp = tmp_path / "doc" / "run1"
        ensure_run_directories(rp)
        assert metadata_dir(rp).exists()
        assert extraction_dir(rp).exists()
        assert chunks_dir(rp).exists()
