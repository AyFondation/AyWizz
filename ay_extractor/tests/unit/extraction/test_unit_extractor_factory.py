# tests/unit/extraction/test_extractor_factory.py — v2
"""Tests for extraction/extractor_factory.py."""

from __future__ import annotations

import pytest

from ayextractor.extraction.extractor_factory import create_extractor, supported_extensions
from ayextractor.extraction.docx_extractor import DocxExtractor
from ayextractor.extraction.epub_extractor import EpubExtractor
from ayextractor.extraction.image_input_extractor import ImageInputExtractor
from ayextractor.extraction.md_extractor import MdExtractor
from ayextractor.extraction.txt_extractor import TxtExtractor


class TestCreateExtractor:
    def test_md(self):
        ext = create_extractor("md")
        assert isinstance(ext, MdExtractor)

    def test_txt(self):
        ext = create_extractor("txt")
        assert isinstance(ext, TxtExtractor)

    def test_pdf(self):
        ext = create_extractor("pdf")
        assert ext.supported_extensions == [".pdf"]

    def test_epub(self):
        ext = create_extractor("epub")
        assert isinstance(ext, EpubExtractor)

    def test_docx(self):
        ext = create_extractor("docx")
        assert isinstance(ext, DocxExtractor)

    def test_png_image(self):
        ext = create_extractor("png")
        assert isinstance(ext, ImageInputExtractor)
        assert ext.requires_vision is True

    def test_jpg_image(self):
        ext = create_extractor("jpg")
        assert isinstance(ext, ImageInputExtractor)

    def test_webp_image(self):
        ext = create_extractor("webp")
        assert isinstance(ext, ImageInputExtractor)

    def test_unsupported(self):
        with pytest.raises((ValueError, Exception)):
            create_extractor("xyz_unsupported")

    def test_supported_extensions_list(self):
        exts = supported_extensions()
        assert ".md" in exts
        assert ".pdf" in exts
        assert ".epub" in exts
        assert ".docx" in exts
        assert ".png" in exts
