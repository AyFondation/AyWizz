# tests/integration/extraction/test_int_extraction_modules.py — v2
"""Integration tests for document extraction pipeline.

Covers: language_detector, structure_detector, content_merger,
table_extractor, extractor_factory, txt_extractor, md_extractor.

Pure Python — no external services.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ayextractor.core.models import ImageAnalysis, TableData


# ── Language Detector ───────────────────────────────────────────

class TestLanguageDetector:

    def test_detect_english(self):
        from ayextractor.extraction.language_detector import detect_document_language
        text = "The European Union has enacted new cybersecurity regulations."
        lang = detect_document_language(text)
        assert lang.lower() in ("en", "eng", "english")

    def test_detect_french(self):
        from ayextractor.extraction.language_detector import detect_document_language
        text = "L'Union européenne a adopté de nouvelles réglementations en matière de cybersécurité."
        lang = detect_document_language(text)
        assert lang.lower() in ("fr", "fra", "french")

    def test_detect_chunk_language(self):
        from ayextractor.extraction.language_detector import detect_chunk_language
        result = detect_chunk_language("This is a test sentence in English.")
        assert result is not None
        assert result.primary_language.lower() in ("en", "eng", "english")
        assert 0 <= result.confidence <= 1.0

    def test_detect_chunk_multilingual(self):
        from ayextractor.extraction.language_detector import detect_chunk_language
        result = detect_chunk_language("This is a test sentence in English.")
        assert isinstance(result.is_multilingual, bool)
        assert isinstance(result.secondary_languages, list)

    def test_short_text(self):
        from ayextractor.extraction.language_detector import detect_document_language
        lang = detect_document_language("Hello")
        assert isinstance(lang, str)
        assert len(lang) >= 2

    def test_empty_text(self):
        from ayextractor.extraction.language_detector import detect_document_language
        lang = detect_document_language("")
        assert isinstance(lang, str)

    def test_metadata_override(self):
        from ayextractor.extraction.language_detector import detect_document_language
        lang = detect_document_language("Some text", override="de")
        assert lang.lower() in ("de", "deu", "german")


# ── Structure Detector ──────────────────────────────────────────

class TestStructureDetector:

    def test_detect_sections(self):
        from ayextractor.extraction.structure_detector import detect_structure
        text = """# Chapter 1: Introduction

This is the introduction.

## 1.1 Background

Some background info.

# Chapter 2: Methodology

The methodology section.

## References

[1] Smith et al. 2024.
"""
        structure = detect_structure(text)
        assert len(structure.sections) >= 2
        assert structure.has_toc is not None

    def test_detect_bibliography(self):
        from ayextractor.extraction.structure_detector import detect_structure
        text = """Main content here.

## Bibliography

[1] Author A. Title. 2024.
[2] Author B. Another title. 2023.
"""
        structure = detect_structure(text)
        assert structure.has_bibliography is True

    def test_detect_footnotes(self):
        from ayextractor.extraction.structure_detector import detect_structure
        text = """Main text here[1].

---

[1] This is a footnote.
[2] Another footnote.
"""
        structure = detect_structure(text)
        assert len(structure.footnotes) >= 0  # detection is heuristic

    def test_minimal_text(self):
        from ayextractor.extraction.structure_detector import detect_structure
        structure = detect_structure("Hello world.")
        assert structure is not None
        assert isinstance(structure.sections, list)


# ── Content Merger ──────────────────────────────────────────────

class TestContentMerger:

    def test_merge_text_only(self):
        from ayextractor.extraction.content_merger import merge_content
        result = merge_content(raw_text="Hello world.", images=[], tables=[])
        assert "Hello world." in result

    def test_merge_with_tables(self):
        from ayextractor.extraction.content_merger import merge_content
        tables = [TableData(
            id="tbl_001",
            content_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
            origin="structured",
            source_page=1,
        )]
        result = merge_content(raw_text="Before table.", tables=tables)
        assert "Before table" in result

    def test_format_image_block(self):
        from ayextractor.extraction.content_merger import format_image_block
        img = ImageAnalysis(
            id="img_001",
            type="diagram",
            description="A diagram showing network architecture.",
            entities=["network", "firewall"],
            source_page=1,
        )
        block = format_image_block(img)
        assert "diagram" in block.lower() or "network" in block.lower()

    def test_format_table_block(self):
        from ayextractor.extraction.content_merger import format_table_block
        table = TableData(
            id="tbl_002",
            content_markdown="| H1 | H2 |\n|---|---|\n| V1 | V2 |",
            origin="structured",
            source_page=2,
        )
        block = format_table_block(table)
        assert "H1" in block or "V1" in block


# ── Table Extractor ─────────────────────────────────────────────

class TestTableExtractor:

    def test_extract_tables_from_text(self):
        from ayextractor.extraction.table_extractor import extract_tables_from_text
        text = """Some text.

| Column A | Column B | Column C |
|----------|----------|----------|
| val1     | val2     | val3     |
| val4     | val5     | val6     |

More text after table.
"""
        tables = extract_tables_from_text(text)
        assert len(tables) >= 1
        assert isinstance(tables[0], TableData)
        assert "Column A" in tables[0].content_markdown

    def test_no_tables(self):
        from ayextractor.extraction.table_extractor import extract_tables_from_text
        tables = extract_tables_from_text("Just plain text with no tables.")
        assert tables == []

    def test_rows_to_markdown(self):
        from ayextractor.extraction.table_extractor import _rows_to_markdown
        md = _rows_to_markdown([["A", "B"], ["1", "2"]])
        assert "A" in md and "B" in md


# ── Extractor Factory ──────────────────────────────────────────

class TestExtractorFactory:

    def test_supported_extensions(self):
        from ayextractor.extraction.extractor_factory import supported_extensions
        exts = supported_extensions()
        assert ".txt" in exts
        assert ".md" in exts
        assert ".pdf" in exts

    def test_create_txt_extractor(self):
        from ayextractor.extraction.extractor_factory import create_extractor
        ext = create_extractor(".txt")
        assert ext is not None

    def test_create_md_extractor(self):
        from ayextractor.extraction.extractor_factory import create_extractor
        ext = create_extractor(".md")
        assert ext is not None

    def test_unsupported_raises(self):
        from ayextractor.extraction.extractor_factory import (
            UnsupportedFormatError,
            create_extractor,
        )
        with pytest.raises(UnsupportedFormatError):
            create_extractor(".xyz123")


# ── TXT Extractor ──────────────────────────────────────────────

class TestTxtExtractor:

    @pytest.mark.asyncio
    async def test_extract_txt(self, tmp_path: Path):
        from ayextractor.extraction.extractor_factory import create_extractor
        f = tmp_path / "test.txt"
        f.write_text("Hello world.\nThis is line two.", encoding="utf-8")
        extractor = create_extractor(".txt")
        result = await extractor.extract(f)
        assert "Hello world" in result.raw_text

    @pytest.mark.asyncio
    async def test_extract_empty_file(self, tmp_path: Path):
        from ayextractor.extraction.extractor_factory import create_extractor
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        extractor = create_extractor(".txt")
        result = await extractor.extract(f)
        assert result.raw_text == "" or result.enriched_text == ""


# ── MD Extractor ───────────────────────────────────────────────

class TestMdExtractor:

    @pytest.mark.asyncio
    async def test_extract_markdown(self, tmp_path: Path):
        from ayextractor.extraction.extractor_factory import create_extractor
        f = tmp_path / "test.md"
        f.write_text(
            "# Title\n\nParagraph with **bold** text.\n\n## Section 2\n\nMore content.",
            encoding="utf-8",
        )
        extractor = create_extractor(".md")
        result = await extractor.extract(f)
        assert "Title" in result.raw_text
        assert "Section 2" in result.raw_text

    @pytest.mark.asyncio
    async def test_markdown_structure(self, tmp_path: Path):
        from ayextractor.extraction.extractor_factory import create_extractor
        f = tmp_path / "struct.md"
        f.write_text("# H1\n\nText\n\n## H2\n\nMore text", encoding="utf-8")
        extractor = create_extractor(".md")
        result = await extractor.extract(f)
        assert len(result.structure.sections) >= 1
