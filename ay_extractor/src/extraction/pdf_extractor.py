# src/extraction/pdf_extractor.py — v1
"""PDF extractor using PyMuPDF (fitz).

Extracts text, embedded images, and structured tables.
Requires the 'pymupdf' package.
See spec §4 pipeline step 1b.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ayextractor.core.models import (
    DocumentStructure,
    ExtractionResult,
    ImageAnalysis,
    TableData,
)
from ayextractor.extraction.base_extractor import BaseExtractor
from ayextractor.extraction.structure_detector import detect_structure
from ayextractor.extraction.table_extractor import extract_tables_from_pdf_page

logger = logging.getLogger(__name__)


class PdfExtractor(BaseExtractor):
    """Extractor for PDF files using PyMuPDF."""

    @property
    def supported_extensions(self) -> list[str]:
        return [".pdf"]

    @property
    def requires_vision(self) -> bool:
        return True

    async def extract(self, content: bytes | str | Path) -> ExtractionResult:
        """Extract text, images, and tables from a PDF document."""
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise ImportError(
                "pymupdf package required for PDF extraction: pip install pymupdf"
            ) from e

        doc = self._open_document(content, fitz)

        raw_text_parts: list[str] = []
        images: list[ImageAnalysis] = []
        tables: list[TableData] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            page_number = page_num + 1

            # Extract text
            text = page.get_text("text")
            raw_text_parts.append(text)

            # Extract images (metadata only — actual analysis is done by image_analyzer)
            for img_index, img_info in enumerate(page.get_images(full=True)):
                xref = img_info[0]
                img_id = f"img_p{page_number}_{img_index + 1:02d}"
                images.append(
                    ImageAnalysis(
                        id=img_id,
                        type="photo",
                        description=f"[Embedded image on page {page_number}, pending analysis]",
                        source_page=page_number,
                    )
                )

            # Extract tables (if PyMuPDF supports it)
            try:
                page_tables = page.find_tables()
                if page_tables and page_tables.tables:
                    for table in page_tables.tables:
                        raw_data = table.extract()
                        if raw_data:
                            tables.extend(
                                extract_tables_from_pdf_page([raw_data], page_number)
                            )
            except Exception:
                logger.debug("Table extraction not available for page %d", page_number)

        doc.close()
        raw_text = "\n".join(raw_text_parts)
        structure = detect_structure(raw_text)

        return ExtractionResult(
            raw_text=raw_text,
            enriched_text=raw_text,
            images=images,
            tables=tables,
            structure=structure,
            language="en",
        )

    @staticmethod
    def _open_document(content: bytes | str | Path, fitz_module: object) -> object:
        """Open PDF from various input types."""
        fitz_mod = fitz_module  # type: ignore[assignment]
        if isinstance(content, Path):
            return fitz_mod.open(str(content))
        if isinstance(content, str):
            return fitz_mod.open(content)
        return fitz_mod.open(stream=content, filetype="pdf")
