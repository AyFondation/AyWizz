# tests/e2e/test_func_chunking.py — v2
"""Functional E2E tests — chunking from extracted documents.

Changelog:
    v2: Fix missing await on chunker.chunk() (async method).
        Add conditional skip for DOCX tests when python-docx is not installed.
    v1: Initial version.

Validates that the structural chunker produces correct, ordered, non-overlapping
chunks from known extraction output. Uses golden files for deterministic input.

No LLM required — pure algorithmic tests.

Coverage:
    - Chunk count within expected range
    - Chunk ordering and ID uniqueness
    - Chunk content coverage (key fragments appear in at least one chunk)
    - Chunk metadata integrity (source_file, char_count, etc.)
    - Cross-format chunking consistency (PDF vs DOCX → similar chunks)
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e]


# =====================================================================
#  HELPERS
# =====================================================================

async def _extract_and_chunk(extractor, file_path, chunk_target_size=500):
    """Extract document then chunk the result."""
    from ayextractor.chunking.structural_chunker import StructuralChunker
    from ayextractor.config.settings import Settings

    result = await extractor.extract(file_path)

    settings = Settings.model_construct(
        chunk_target_size=chunk_target_size,
        chunk_overlap=0,
    )
    chunker = StructuralChunker(settings=settings)
    # FIX v2: chunker.chunk() is async — must be awaited
    chunks = await chunker.chunk(
        text=result.enriched_text,
        structure=result.structure,
        source_file=str(file_path),
    )
    return result, chunks


def _skip_if_no_docx():
    """Skip test if python-docx is not installed."""
    try:
        import docx  # noqa: F401
    except ImportError:
        pytest.skip("python-docx not installed — pip install python-docx")


# =====================================================================
#  PDF CHUNKING
# =====================================================================

class TestPdfChunking:
    """Structural chunking of the golden NIS2 PDF."""

    @pytest.mark.asyncio
    async def test_produces_chunks(self, nis2_pdf_path, expected_chunks):
        """Chunker produces a reasonable number of chunks from the PDF."""
        from ayextractor.extraction.pdf_extractor import PdfExtractor

        _, chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)

        assert len(chunks) >= expected_chunks["min_chunk_count"], (
            f"Too few chunks: {len(chunks)} < {expected_chunks['min_chunk_count']}"
        )
        assert len(chunks) <= expected_chunks["max_chunk_count"], (
            f"Too many chunks: {len(chunks)} > {expected_chunks['max_chunk_count']}"
        )

    @pytest.mark.asyncio
    async def test_chunk_ids_unique(self, nis2_pdf_path, expected_chunks):
        """All chunk IDs are unique."""
        from ayextractor.extraction.pdf_extractor import PdfExtractor

        _, chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)

        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids)), (
            f"Duplicate chunk IDs found: {[x for x in ids if ids.count(x) > 1]}"
        )

    @pytest.mark.asyncio
    async def test_chunks_are_ordered(self, nis2_pdf_path, expected_chunks):
        """Chunk positions are monotonically increasing."""
        from ayextractor.extraction.pdf_extractor import PdfExtractor

        _, chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)

        positions = [c.position for c in chunks]
        assert positions == sorted(positions), (
            f"Chunks not ordered: {positions}"
        )

    @pytest.mark.asyncio
    async def test_every_chunk_has_content(self, nis2_pdf_path, expected_chunks):
        """Every chunk has minimum required fields."""
        from ayextractor.extraction.pdf_extractor import PdfExtractor

        _, chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)

        rules = expected_chunks["every_chunk_must"]
        for i, chunk in enumerate(chunks):
            if rules.get("has_id"):
                assert chunk.id, f"Chunk {i} has no ID"
            if rules.get("has_content"):
                assert chunk.content.strip(), f"Chunk {i} has empty content"
            if rules.get("has_source_file"):
                assert chunk.source_file, f"Chunk {i} has no source_file"
            if rules.get("min_char_count"):
                assert chunk.char_count >= rules["min_char_count"], (
                    f"Chunk {i} too short: {chunk.char_count} < {rules['min_char_count']}"
                )

    @pytest.mark.asyncio
    async def test_key_content_in_chunks(self, nis2_pdf_path, expected_chunks):
        """Key content fragments appear in at least one chunk."""
        from ayextractor.extraction.pdf_extractor import PdfExtractor

        _, chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)

        all_content = " ".join(c.content for c in chunks)
        for fragment in expected_chunks["at_least_one_chunk_must_contain"]:
            assert fragment in all_content, (
                f"Key fragment not found in any chunk: {fragment!r}"
            )

    @pytest.mark.asyncio
    async def test_smaller_target_produces_more_chunks(self, nis2_pdf_path):
        """Reducing chunk_target_size increases chunk count."""
        from ayextractor.extraction.pdf_extractor import PdfExtractor

        ext = PdfExtractor()
        _, chunks_large = await _extract_and_chunk(ext, nis2_pdf_path, chunk_target_size=2000)
        _, chunks_small = await _extract_and_chunk(ext, nis2_pdf_path, chunk_target_size=300)

        assert len(chunks_small) > len(chunks_large), (
            f"Smaller target ({len(chunks_small)} chunks) should produce more chunks "
            f"than larger target ({len(chunks_large)} chunks)"
        )


# =====================================================================
#  DOCX CHUNKING
# =====================================================================

class TestDocxChunking:
    """Structural chunking of the golden NIS2 DOCX."""

    @pytest.mark.asyncio
    async def test_produces_chunks(self, nis2_docx_path, expected_chunks):
        """Chunker produces a reasonable number of chunks from DOCX."""
        _skip_if_no_docx()
        from ayextractor.extraction.docx_extractor import DocxExtractor

        _, chunks = await _extract_and_chunk(DocxExtractor(), nis2_docx_path)

        assert len(chunks) >= expected_chunks["min_chunk_count"]
        assert len(chunks) <= expected_chunks["max_chunk_count"]

    @pytest.mark.asyncio
    async def test_chunk_ids_unique(self, nis2_docx_path):
        """DOCX chunks have unique IDs."""
        _skip_if_no_docx()
        from ayextractor.extraction.docx_extractor import DocxExtractor

        _, chunks = await _extract_and_chunk(DocxExtractor(), nis2_docx_path)

        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_key_content_in_chunks(self, nis2_docx_path, expected_chunks):
        """Key content appears in DOCX chunks."""
        _skip_if_no_docx()
        from ayextractor.extraction.docx_extractor import DocxExtractor

        _, chunks = await _extract_and_chunk(DocxExtractor(), nis2_docx_path)

        all_content = " ".join(c.content for c in chunks)
        for fragment in expected_chunks["at_least_one_chunk_must_contain"]:
            assert fragment in all_content, (
                f"DOCX chunk missing: {fragment!r}"
            )


# =====================================================================
#  CROSS-FORMAT CHUNKING CONSISTENCY
# =====================================================================

class TestCrossFormatChunking:
    """Same document in PDF vs DOCX → similar chunking output."""

    @pytest.mark.asyncio
    async def test_both_formats_produce_chunks(self, nis2_pdf_path, nis2_docx_path):
        """Both formats produce at least 2 chunks."""
        _skip_if_no_docx()
        from ayextractor.extraction.pdf_extractor import PdfExtractor
        from ayextractor.extraction.docx_extractor import DocxExtractor

        _, pdf_chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)
        _, docx_chunks = await _extract_and_chunk(DocxExtractor(), nis2_docx_path)

        assert len(pdf_chunks) >= 2
        assert len(docx_chunks) >= 2

    @pytest.mark.asyncio
    async def test_same_key_content_covered(
        self, nis2_pdf_path, nis2_docx_path, expected_chunks
    ):
        """Both formats cover the same key content fragments."""
        _skip_if_no_docx()
        from ayextractor.extraction.pdf_extractor import PdfExtractor
        from ayextractor.extraction.docx_extractor import DocxExtractor

        _, pdf_chunks = await _extract_and_chunk(PdfExtractor(), nis2_pdf_path)
        _, docx_chunks = await _extract_and_chunk(DocxExtractor(), nis2_docx_path)

        pdf_text = " ".join(c.content for c in pdf_chunks)
        docx_text = " ".join(c.content for c in docx_chunks)

        for fragment in expected_chunks["at_least_one_chunk_must_contain"]:
            assert fragment in pdf_text, f"PDF chunks missing: {fragment!r}"
            assert fragment in docx_text, f"DOCX chunks missing: {fragment!r}"