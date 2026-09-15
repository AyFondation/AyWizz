# tests/unit/api/test_facade.py — v1
"""Tests for api.facade — public entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ayextractor.api.facade import (
    _apply_overrides,
    _augment_text_with_images,
    _chunks_to_jsonl,
    _embedding_text,
    _generate_document_id,
    _generate_run_id,
    _resolve_content_bytes,
    analyze,
)
from ayextractor.api.models import (
    AnalysisResult,
    ConfigOverrides,
    DocumentInput,
    Metadata,
)

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_document(content: str = "test content", fmt: str = "md") -> DocumentInput:
    return DocumentInput(content=content, format=fmt, filename="test.md")


def _make_metadata(**kwargs) -> Metadata:
    defaults = {"document_type": "report", "output_path": Path("/tmp/out")}
    defaults.update(kwargs)
    return Metadata(**defaults)


# ---------------------------------------------------------------------------
# Unit tests — internal helpers
# ---------------------------------------------------------------------------

class TestGenerateDocumentId:
    def test_format(self):
        doc_id = _generate_document_id()
        parts = doc_id.split("_")
        assert len(parts) == 3  # date_time_uuid
        assert len(parts[0]) == 8  # yyyymmdd
        assert len(parts[1]) == 6  # hhmmss
        assert len(parts[2]) == 8  # uuid short

    def test_uniqueness(self):
        ids = {_generate_document_id() for _ in range(50)}
        assert len(ids) == 50


class TestGenerateRunId:
    def test_format(self):
        run_id = _generate_run_id("doc_123")
        parts = run_id.split("_")
        assert len(parts) >= 2
        assert len(parts[0]) == 8  # yyyymmdd

    def test_deterministic_for_same_input_and_time(self):
        # uuid5 is deterministic for the same name, so same doc+minute → same id
        rid1 = _generate_run_id("doc_a")
        rid2 = _generate_run_id("doc_a")
        assert rid1 == rid2


class TestResolveContentBytes:
    def test_bytes_passthrough(self):
        doc = DocumentInput(content=b"hello", format="txt", filename="t.txt")
        assert _resolve_content_bytes(doc) == b"hello"

    def test_str_encoded(self):
        doc = DocumentInput(content="héllo", format="md", filename="t.md")
        assert _resolve_content_bytes(doc) == "héllo".encode()

    def test_path_read(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_bytes(b"content")
        doc = DocumentInput(content=f, format="txt", filename="doc.txt")
        assert _resolve_content_bytes(doc) == b"content"

    def test_multi_path(self, tmp_path: Path):
        f1 = tmp_path / "a.png"
        f2 = tmp_path / "b.png"
        f1.write_bytes(b"img1")
        f2.write_bytes(b"img2")
        doc = DocumentInput(content=[f1, f2], format="image", filename="imgs")
        assert _resolve_content_bytes(doc) == b"img1img2"


class TestApplyOverrides:
    def test_no_overrides(self):
        from ayextractor.config.settings import Settings
        s = Settings()
        m = _make_metadata()
        result = _apply_overrides(s, m)
        assert result is s  # Same object, not modified

    def test_with_overrides(self):
        from ayextractor.config.settings import Settings
        s = Settings()
        overrides = ConfigOverrides(chunk_target_size=500)
        m = _make_metadata(config_overrides=overrides)
        result = _apply_overrides(s, m)
        assert result.chunk_target_size == 500


# ---------------------------------------------------------------------------
# Integration test — analyze()
# ---------------------------------------------------------------------------

class TestAnalyze:
    # D-020 v1 strip: analyze() no longer drives the Phase-3 `DocumentPipeline`
    # DAG (synthesis / communities / graph). It extracts + chunks and (config-
    # gated) runs the Phase-2 text-enrichment agents. These tests exercise the
    # REAL path on the minimal tier (no LLM needed) ; enrichment-on behaviour is
    # covered in tests/unit/pipeline/test_unit_enrichment.py.
    @pytest.mark.asyncio
    async def test_analyze_returns_result(self, tmp_path: Path):
        doc = _make_document(content="# Title\n\nSome content to chunk.\n")
        meta = _make_metadata(output_path=tmp_path)  # minimal tier by default
        result = await analyze(doc, meta)

        assert isinstance(result, AnalysisResult)
        assert result.document_id
        assert result.run_id
        assert result.chunks_count >= 1
        assert result.summary == ""  # minimal tier → no enrichment summary

    @pytest.mark.asyncio
    async def test_analyze_with_custom_document_id(self, tmp_path: Path):
        doc = _make_document(content="# Title\n\nSome content.\n")
        meta = _make_metadata(output_path=tmp_path, document_id="custom_doc_42")
        result = await analyze(doc, meta)

        assert result.document_id == "custom_doc_42"


# ---------------------------------------------------------------------------
# Contextual retrieval — `_embedding_text` / `search_text`
# ---------------------------------------------------------------------------


class _Sec:
    def __init__(self, title: str) -> None:
        self.title = title


class _Chunk:
    """Minimal stand-in carrying only the fields `_embedding_text` /
    `_chunks_to_jsonl` read."""

    def __init__(
        self,
        *,
        content: str = "body text",
        source_sections: list[_Sec] | None = None,
        global_summary: str | None = None,
        context_summary: str | None = None,
    ) -> None:
        self.id = "chunk_000"
        self.position = 0
        self.content = content
        self.original_content = content
        self.context_summary = context_summary
        self.global_summary = global_summary
        self.source_sections = source_sections or []
        self.byte_offset_start = 0
        self.byte_offset_end = len(content)
        self.token_count_est = max(1, len(content) // 4)
        self.embedded_images: list[str] = []
        self.embedded_tables: list[str] = []


class TestEmbeddingText:
    def test_prepends_section_path(self):
        c = _Chunk(
            content="The valve opens at 5 bar.",
            source_sections=[_Sec("Chapter 2"), _Sec("2.3 Hydraulics")],
        )
        assert _embedding_text(c) == "Chapter 2 > 2.3 Hydraulics\n\nThe valve opens at 5 bar."

    def test_falls_back_to_content_without_sections(self):
        # No structure → bare content (no regression vs. content-only embedding).
        assert _embedding_text(_Chunk(content="Bare body.", source_sections=[])) == "Bare body."

    def test_excludes_global_and_cumulative_summaries(self):
        # The (near-)identical global/cumulative summaries must NOT enter the
        # vector text — they would collapse discrimination toward the centroid.
        c = _Chunk(
            content="Body.",
            source_sections=[_Sec("S1")],
            global_summary="WHOLE DOC SUMMARY",
            context_summary="CUMULATIVE SUMMARY SO FAR",
        )
        out = _embedding_text(c)
        assert "WHOLE DOC SUMMARY" not in out
        assert "CUMULATIVE SUMMARY SO FAR" not in out
        assert out == "S1\n\nBody."

    def test_skips_empty_section_titles(self):
        c = _Chunk(content="Body.", source_sections=[_Sec(""), _Sec("Real")])
        assert _embedding_text(c) == "Real\n\nBody."


class TestChunksToJsonlSearchText:
    def test_search_text_inlined_and_equals_embedding_text(self):
        c = _Chunk(content="Body.", source_sections=[_Sec("Intro")])
        record = json.loads(_chunks_to_jsonl([c], run_id="run1"))
        assert record["search_text"] == _embedding_text(c) == "Intro\n\nBody."


class _Img:
    def __init__(self, id: str, description: str, source_page: int | None = None) -> None:
        self.id = id
        self.description = description
        self.source_page = source_page


class TestAugmentTextWithImages:
    def test_appends_captioned_figures(self):
        out = _augment_text_with_images(
            "Body text.",
            [_Img("img_p1_01", "A labelled flowchart of the process.", 1)],
        )
        assert "## Figures (extracted images)" in out
        assert "img_p1_01" in out
        assert "(page 1)" in out
        assert "A labelled flowchart of the process." in out
        assert out.startswith("Body text.")  # original text preserved as prefix

    def test_skips_placeholder_and_failed_captions(self):
        out = _augment_text_with_images(
            "Body.",
            [
                _Img("img_1", "[pending analysis]"),
                _Img("img_2", "[Image analysis skipped — no vision support]"),
            ],
        )
        assert out == "Body."  # nothing real to fold

    def test_no_images_is_identity(self):
        assert _augment_text_with_images("Body.", []) == "Body."
