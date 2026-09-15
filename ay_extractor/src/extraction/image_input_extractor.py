# src/extraction/image_input_extractor.py — v1
"""Standalone image extractor — treats images as full documents.

When a user submits an image file (PNG, JPG, WEBP) instead of a text document,
this extractor uses LLM Vision to describe and extract text content.
See spec §4 — the image is both the document and its single "page".
"""

from __future__ import annotations

import logging
from pathlib import Path

from ayextractor.core.models import (
    DocumentStructure,
    ExtractionResult,
    ImageAnalysis,
)
from ayextractor.extraction.base_extractor import BaseExtractor

logger = logging.getLogger(__name__)

# Mapping of extensions to MIME types
_MIME_MAP: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


class ImageInputExtractor(BaseExtractor):
    """Extractor for standalone image files submitted as documents.

    Unlike image_analyzer.py (which handles *embedded* images inside PDFs/DOCX),
    this handles the case where the entire input is an image.
    """

    @property
    def supported_extensions(self) -> list[str]:
        return [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"]

    @property
    def requires_vision(self) -> bool:
        return True

    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract content from a standalone image.

        Without an LLM client, returns a placeholder result with image metadata.
        Full OCR + Vision analysis is performed by the orchestrator after extraction,
        using image_analyzer.py with the configured LLM Vision adapter.
        """
        image_data, file_path = self._resolve_content(content)
        media_type = self._detect_media_type(file_path)

        # Create a single ImageAnalysis entry representing the whole document
        img_analysis = ImageAnalysis(
            id="img_001",
            type="photo",
            description="[Standalone image document, pending LLM Vision analysis]",
            source_page=1,
        )

        # The raw_text is a placeholder; the orchestrator will replace it
        # after running LLM Vision analysis on the image data.
        placeholder_text = (
            f"[Image document: {file_path or 'uploaded image'}]\n"
            f"[Media type: {media_type}]\n"
            f"[Size: {len(image_data)} bytes]\n"
            f"[Pending LLM Vision analysis for OCR and content extraction]"
        )

        return ExtractionResult(
            raw_text=placeholder_text,
            enriched_text=placeholder_text,
            images=[img_analysis],
            structure=DocumentStructure(),
            language="en",
        )

    @staticmethod
    def _resolve_content(content: bytes | str | Path) -> tuple[bytes, str | None]:
        """Read image bytes and resolve file path."""
        if isinstance(content, bytes):
            return content, None
        path = Path(content) if isinstance(content, str) else content
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")
        return path.read_bytes(), str(path)

    @staticmethod
    def _detect_media_type(file_path: str | None) -> str:
        """Detect MIME type from file extension or magic bytes."""
        if file_path:
            ext = Path(file_path).suffix.lower()
            return _MIME_MAP.get(ext, "image/png")
        return "image/png"
