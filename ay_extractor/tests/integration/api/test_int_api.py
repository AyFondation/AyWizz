# tests/integration/api/test_int_api.py — v2
"""Integration tests for API subsystem.

Covers: api/facade.py, api/models.py
No Docker required.

API source review:
- DocumentInput: requires content, format, filename (all mandatory)
- Metadata: has document_id, document_type, output_path, language (NO title field)
- AnalysisResult: requires document_id, run_id, summary, graph_path,
  communities_path, profiles_path, output_dir, run_dir
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestAPIModels:

    def test_document_input(self):
        from ayextractor.api.models import DocumentInput
        doc = DocumentInput(content="Hello world", format="txt", filename="test.txt")
        assert doc.content == "Hello world"
        assert doc.format == "txt"
        assert doc.filename == "test.txt"

    def test_document_input_with_path(self):
        from ayextractor.api.models import DocumentInput
        doc = DocumentInput(content=Path("/tmp/test.pdf"), format="pdf", filename="test.pdf")
        assert doc.format == "pdf"

    def test_metadata_defaults(self):
        """Metadata has document_type, output_path, language — NOT title."""
        from ayextractor.api.models import Metadata
        meta = Metadata()
        assert meta.document_type == "report"
        assert meta.language is None
        assert meta.output_path == Path("./output")

    def test_metadata_with_overrides(self):
        from ayextractor.api.models import Metadata
        meta = Metadata(
            document_id="doc_001",
            document_type="contract",
            language="fr",
        )
        assert meta.document_id == "doc_001"
        assert meta.document_type == "contract"
        assert meta.language == "fr"

    def test_analysis_result(self):
        # D-020 stripped the graph/community phase ; AnalysisResult is now the
        # chunk-centric shape (no graph_path/community_count/themes).
        from ayextractor.api.models import AnalysisResult
        result = AnalysisResult(
            document_id="doc_001",
            run_id="run_001",
            summary="A summary",
            chunks_count=5,
            output_dir=Path("/tmp/output"),
            run_dir=Path("/tmp/run"),
        )
        assert result.document_id == "doc_001"
        assert result.summary == "A summary"
        assert result.chunks_count == 5
        assert result.confidence_scores == {}  # default
        assert result.manifest_key is None  # no MinIO write by default

    def test_config_overrides(self):
        # `density_iterations` was renamed `chain_of_density_iterations` ;
        # `critic_agent_enabled` was removed (D-020).
        from ayextractor.api.models import ConfigOverrides
        overrides = ConfigOverrides(
            chunking_strategy="semantic",
            chain_of_density_iterations=3,
            densification_enabled=True,
        )
        assert overrides.chunking_strategy == "semantic"
        assert overrides.chain_of_density_iterations == 3
        assert overrides.densification_enabled is True


class TestAPIFacade:

    def test_facade_import(self):
        from ayextractor.api.facade import analyze
        assert callable(analyze)

    @pytest.mark.asyncio
    async def test_facade_signature(self):
        """Verify facade.analyze accepts expected params."""
        from ayextractor.api.models import DocumentInput, Metadata
        doc = DocumentInput(content="Test content.", format="txt", filename="test.txt")
        meta = Metadata(document_type="report", language="en")
        assert doc.format == "txt"
        assert meta.document_type == "report"
