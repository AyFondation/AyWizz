# tests/unit/extraction/test_image_input_extractor.py — v1
"""Tests for extraction/image_input_extractor.py."""

from __future__ import annotations

import pytest
from pathlib import Path

from ayextractor.extraction.image_input_extractor import ImageInputExtractor


class TestImageInputExtractor:
    def test_supported_extensions(self):
        ext = ImageInputExtractor()
        assert ".png" in ext.supported_extensions
        assert ".jpg" in ext.supported_extensions
        assert ".jpeg" in ext.supported_extensions
        assert ".webp" in ext.supported_extensions

    def test_requires_vision(self):
        ext = ImageInputExtractor()
        assert ext.requires_vision is True

    @pytest.mark.asyncio
    async def test_extract_from_bytes(self):
        """Extract from raw bytes produces placeholder result."""
        ext = ImageInputExtractor()
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = await ext.extract(fake_png)
        assert "Image document" in result.raw_text
        assert len(result.images) == 1
        assert result.images[0].id == "img_001"

    @pytest.mark.asyncio
    async def test_extract_from_file(self, tmp_path):
        """Extract from a file path."""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        ext = ImageInputExtractor()
        result = await ext.extract(str(img_file))
        assert "image/png" in result.raw_text
        assert len(result.images) == 1

    @pytest.mark.asyncio
    async def test_extract_file_not_found(self):
        """Non-existent file raises FileNotFoundError."""
        ext = ImageInputExtractor()
        with pytest.raises(FileNotFoundError):
            await ext.extract("/nonexistent/image.png")

    def test_detect_media_type(self):
        assert ImageInputExtractor._detect_media_type("photo.jpg") == "image/jpeg"
        assert ImageInputExtractor._detect_media_type("photo.png") == "image/png"
        assert ImageInputExtractor._detect_media_type("photo.webp") == "image/webp"
        assert ImageInputExtractor._detect_media_type(None) == "image/png"
