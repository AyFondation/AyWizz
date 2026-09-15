# tests/unit/extraction/test_docx_extractor.py — v1
"""Tests for extraction/docx_extractor.py."""

from __future__ import annotations

import pytest
from ayextractor.extraction.docx_extractor import DocxExtractor


class TestDocxExtractor:
    def test_supported_extensions(self):
        ext = DocxExtractor()
        assert ".docx" in ext.supported_extensions

    def test_requires_vision(self):
        ext = DocxExtractor()
        assert ext.requires_vision is False

    async def test_import_error_message(self):
        """When python-docx is not installed, a clear ImportError is raised.

        Blocks the lazy `import docx` inside `extract()` by pinning
        `sys.modules["docx"] = None` (raises ImportError even when the package
        IS installed). Async + await replaces the deprecated
        `get_event_loop().run_until_complete` pattern that broke on Python 3.13."""
        import sys
        docx_mod = sys.modules.get("docx")
        sys.modules["docx"] = None  # type: ignore[assignment]
        try:
            ext = DocxExtractor()
            with pytest.raises(ImportError, match="python-docx"):
                await ext.extract(b"fake")
        finally:
            if docx_mod is not None:
                sys.modules["docx"] = docx_mod
            else:
                sys.modules.pop("docx", None)

    def test_rows_to_markdown(self):
        """Test internal markdown table conversion."""
        rows = [["Name", "Value"], ["A", "1"], ["B", "2"]]
        md = DocxExtractor._rows_to_markdown(rows)
        assert "| Name | Value |" in md
        assert "| --- | --- |" in md
        assert "| A | 1 |" in md

    def test_rows_to_markdown_empty(self):
        assert DocxExtractor._rows_to_markdown([]) == ""
