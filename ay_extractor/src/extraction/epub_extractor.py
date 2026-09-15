# src/extraction/epub_extractor.py — v1
"""EPUB extractor using ebooklib.

Extracts text content and image references from EPUB e-books.
Requires the 'ebooklib' and 'beautifulsoup4' packages.
See spec §4 pipeline step 1b.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ayextractor.core.models import (
    DocumentStructure,
    ExtractionResult,
    ImageAnalysis,
)
from ayextractor.extraction.base_extractor import BaseExtractor
from ayextractor.extraction.structure_detector import detect_structure

logger = logging.getLogger(__name__)


class EpubExtractor(BaseExtractor):
    """Extractor for EPUB files (.epub)."""

    @property
    def supported_extensions(self) -> list[str]:
        return [".epub"]

    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract text and images from EPUB document."""
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError as e:
            raise ImportError(
                "ebooklib package required for EPUB extraction: "
                "pip install ebooklib beautifulsoup4"
            ) from e

        try:
            from bs4 import BeautifulSoup
        except ImportError as e:
            raise ImportError(
                "beautifulsoup4 required for EPUB extraction: pip install beautifulsoup4"
            ) from e

        book = self._open_book(content, epub)

        text_parts: list[str] = []
        images: list[ImageAnalysis] = []
        img_count = 0

        # Extract text from HTML documents in spine order
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            html_content = item.get_content().decode("utf-8", errors="replace")
            soup = BeautifulSoup(html_content, "html.parser")

            # Extract image references
            for img_tag in soup.find_all("img"):
                img_count += 1
                src = img_tag.get("src", "")
                alt = img_tag.get("alt", "")
                images.append(
                    ImageAnalysis(
                        id=f"img_{img_count:03d}",
                        type="photo",
                        description=alt or f"[Embedded EPUB image: {src}]",
                        source_page=None,
                    )
                )

            # Extract text
            text = soup.get_text(separator="\n", strip=True)
            if text.strip():
                text_parts.append(text)

        raw_text = "\n\n".join(text_parts)
        structure = detect_structure(raw_text)

        return ExtractionResult(
            raw_text=raw_text,
            enriched_text=raw_text,
            images=images,
            structure=structure,
            language="en",
        )

    @staticmethod
    def _open_book(content: bytes | str | Path, epub_module: object) -> object:
        """Open EPUB from various input types."""
        epub_mod = epub_module  # type: ignore[assignment]
        if isinstance(content, Path):
            return epub_mod.read_epub(str(content))
        if isinstance(content, str):
            p = Path(content)
            if p.exists() and p.is_file():
                return epub_mod.read_epub(content)
            raise FileNotFoundError(f"EPUB file not found: {content}")
        # bytes: write to temp file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            return epub_mod.read_epub(tmp.name)
