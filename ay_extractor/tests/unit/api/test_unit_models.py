# tests/unit/api/test_models.py — v1
"""Tests for api/models.py — API-level input/output models."""

from __future__ import annotations

from pathlib import Path

from ayextractor.api.models import (
    AnalysisResult,
    ConfigOverrides,
    DocumentInput,
    Metadata,
)


class TestDocumentInput:
    def test_create_with_string(self):
        d = DocumentInput(content="text content", format="md", filename="doc.md")
        assert d.format == "md"

    def test_create_with_path(self):
        d = DocumentInput(content=Path("/tmp/file.pdf"), format="pdf", filename="file.pdf")
        assert isinstance(d.content, Path)

    def test_create_with_bytes(self):
        d = DocumentInput(content=b"binary", format="pdf", filename="file.pdf")
        assert isinstance(d.content, bytes)


class TestMetadata:
    def test_defaults(self):
        m = Metadata()
        assert m.document_id is None
        assert m.document_type == "report"
        assert m.language is None

    def test_with_overrides(self):
        # D-020 v1: `density_iterations` was renamed `chain_of_density_iterations`.
        overrides = ConfigOverrides(chunk_target_size=4000, chain_of_density_iterations=3)
        m = Metadata(config_overrides=overrides)
        assert m.config_overrides.chunk_target_size == 4000
        assert m.config_overrides.chain_of_density_iterations == 3


class TestConfigOverrides:
    def test_all_none_by_default(self):
        co = ConfigOverrides()
        assert co.llm_assignments is None
        assert co.chunking_strategy is None

    def test_partial_override(self):
        # D-020 v1 strip: Phase-3 fields (entity_similarity_threshold, …) were
        # removed; the surviving Phase-2 toggles are the per-agent enables.
        co = ConfigOverrides(
            chunking_strategy="semantic",
            summarization_enabled=True,
        )
        assert co.chunking_strategy == "semantic"
        assert co.summarization_enabled is True
        # Unset toggles stay None (so the tier preset wins in resolution).
        assert co.chain_of_density_iterations is None
        assert co.densification_enabled is None


class TestAnalysisResult:
    def test_create_minimal(self):
        # D-020 v1 strip: Phase-3 outputs (graph_path, communities_path,
        # profiles_path, community_count, themes) were removed — the result is
        # now scoped to chunks + summary + MinIO artifact keys.
        ar = AnalysisResult(
            document_id="20260207_140000_abc",
            run_id="20260207_1615_xyz",
            summary="Test summary",
            output_dir=Path("/out"),
            run_dir=Path("/out/runs/r1"),
        )
        assert ar.summary == "Test summary"
        assert ar.chunks_count == 0
        assert ar.manifest_key is None  # set only when MinIO artifacts are written
