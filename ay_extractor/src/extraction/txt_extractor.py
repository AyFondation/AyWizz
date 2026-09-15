# src/extraction/txt_extractor.py — v2
"""Plain text extractor — passthrough with minimal processing."""

from __future__ import annotations

from pathlib import Path

from ayextractor.core.models import DocumentStructure, ExtractionResult
from ayextractor.extraction.base_extractor import BaseExtractor


class TxtExtractor(BaseExtractor):
    """Extractor for plain text files (.txt)."""

    @property
    def supported_extensions(self) -> list[str]:
        return [".txt"]

    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract text from plain text file."""
        text = self._read_content(content)

        return ExtractionResult(
            raw_text=text,
            enriched_text=text,
            structure=DocumentStructure(),
            language="en",
        )

    @staticmethod
    def _read_content(content: bytes | str | Path) -> str:
        if isinstance(content, Path):
            return content.read_text(encoding="utf-8")
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        # str: check if it's a file path
        p = Path(content)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8")
        return content
