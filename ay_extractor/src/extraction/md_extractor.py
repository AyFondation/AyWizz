# src/extraction/md_extractor.py — v2
"""Markdown extractor — text + image references + inline tables."""

from __future__ import annotations

import re
from pathlib import Path

from ayextractor.core.models import DocumentStructure, ExtractionResult
from ayextractor.extraction.base_extractor import BaseExtractor
from ayextractor.extraction.structure_detector import detect_structure
from ayextractor.extraction.table_extractor import extract_tables_from_text


class MdExtractor(BaseExtractor):
    """Extractor for Markdown files (.md, .markdown)."""

    @property
    def supported_extensions(self) -> list[str]:
        return [".md", ".markdown"]

    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract text, structure, and tables from Markdown."""
        text = self._read_content(content)
        structure = detect_structure(text)
        tables = extract_tables_from_text(text)
        image_refs = self._extract_image_refs(text)

        return ExtractionResult(
            raw_text=text,
            enriched_text=text,
            tables=tables,
            structure=structure,
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

    @staticmethod
    def _extract_image_refs(text: str) -> list[str]:
        """Extract image references from Markdown syntax."""
        pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
        return [match.group(2) for match in pattern.finditer(text)]
