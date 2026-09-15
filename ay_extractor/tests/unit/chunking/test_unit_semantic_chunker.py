# tests/unit/chunking/test_semantic_chunker.py — v1
"""Tests for chunking/semantic_chunker.py."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from ayextractor.chunking.semantic_chunker import SemanticChunker


class TestSemanticChunkerProperties:
    def test_strategy_name(self):
        chunker = SemanticChunker()
        assert chunker.strategy_name == "semantic"

    def test_default_target_size(self):
        chunker = SemanticChunker()
        assert chunker._target == 2000

    def test_custom_target_size(self):
        from ayextractor.config.settings import Settings
        s = Settings(_env_file=None, chunk_target_size=500)
        chunker = SemanticChunker(settings=s)
        assert chunker._target == 500


class TestSemanticChunkerFallback:
    """Tests for fallback mode (no embedder)."""

    @pytest.mark.asyncio
    async def test_empty_text(self):
        chunker = SemanticChunker()
        chunks = await chunker.chunk("")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        chunker = SemanticChunker()
        chunks = await chunker.chunk("   \n  \n  ")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_single_paragraph(self):
        chunker = SemanticChunker()
        text = "This is a single paragraph of text for testing purposes."
        chunks = await chunker.chunk(text)
        assert len(chunks) == 1
        assert "single paragraph" in chunks[0].content

    @pytest.mark.asyncio
    async def test_multiple_paragraphs(self):
        chunker = SemanticChunker()
        paragraphs = ["Paragraph one content here."] * 5
        text = "\n\n".join(paragraphs)
        chunks = await chunker.chunk(text)
        assert len(chunks) >= 1
        # All content should be preserved
        total_content = " ".join(c.content for c in chunks)
        assert "Paragraph one content here" in total_content

    @pytest.mark.asyncio
    async def test_chunk_splitting_by_size(self):
        """With small target, text should be split into multiple chunks."""
        from ayextractor.config.settings import Settings
        s = Settings(_env_file=None, chunk_target_size=50)
        chunker = SemanticChunker(settings=s)
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph with more text."
        chunks = await chunker.chunk(text)
        assert len(chunks) >= 2

    @pytest.mark.asyncio
    async def test_chunk_ids_sequential(self):
        chunker = SemanticChunker()
        text = "Para 1.\n\nPara 2.\n\nPara 3."
        chunks = await chunker.chunk(text)
        for i, chunk in enumerate(chunks):
            assert chunk.id == f"chunk_{i:03d}"
            assert chunk.position == i

    @pytest.mark.asyncio
    async def test_chunk_linking(self):
        """Chunks should have preceding/following IDs set correctly."""
        from ayextractor.config.settings import Settings
        s = Settings(_env_file=None, chunk_target_size=20)
        chunker = SemanticChunker(settings=s)
        text = "First.\n\nSecond.\n\nThird."
        chunks = await chunker.chunk(text)
        if len(chunks) >= 2:
            assert chunks[0].preceding_chunk_id is None
            assert chunks[0].following_chunk_id == chunks[1].id
            assert chunks[-1].following_chunk_id is None
            assert chunks[-1].preceding_chunk_id == chunks[-2].id

    @pytest.mark.asyncio
    async def test_source_file(self):
        chunker = SemanticChunker()
        chunks = await chunker.chunk("Some text.", source_file="doc.pdf")
        assert chunks[0].source_file == "doc.pdf"

    @pytest.mark.asyncio
    async def test_fingerprint_unique(self):
        from ayextractor.config.settings import Settings
        s = Settings(_env_file=None, chunk_target_size=30)
        chunker = SemanticChunker(settings=s)
        text = "Alpha.\n\nBravo.\n\nCharlie."
        chunks = await chunker.chunk(text)
        fingerprints = [c.fingerprint for c in chunks]
        assert len(fingerprints) == len(set(fingerprints))

    @pytest.mark.asyncio
    async def test_word_count(self):
        chunker = SemanticChunker()
        text = "One two three four five."
        chunks = await chunker.chunk(text)
        assert chunks[0].word_count == 5


class TestAtomicBlocks:
    """Atomic IMAGE_CONTENT / TABLE_CONTENT blocks should be kept intact."""

    @pytest.mark.asyncio
    async def test_atomic_block_preserved(self):
        from ayextractor.config.settings import Settings
        s = Settings(_env_file=None, chunk_target_size=50)
        chunker = SemanticChunker(settings=s)
        text = (
            "Before text.\n\n"
            "<<<IMAGE_CONTENT id=img_001\nA complex diagram.\n<<<END_IMAGE_CONTENT>>>\n\n"
            "After text."
        )
        chunks = await chunker.chunk(text)
        # The atomic block should appear intact in exactly one chunk
        all_content = "\n\n".join(c.content for c in chunks)
        assert "<<<IMAGE_CONTENT" in all_content
        assert "<<<END_IMAGE_CONTENT>>>" in all_content


class TestSemanticWithEmbedder:
    """Tests with a mocked embedder to exercise similarity-based splitting."""

    @pytest.mark.asyncio
    async def test_with_embedder(self):
        """With embedder, should use similarity-based merging."""
        mock_embedder = AsyncMock()
        # Return embeddings that are similar for para 0-1 but different for para 2
        mock_embedder.embed_texts = AsyncMock(return_value=[
            [1.0, 0.0, 0.0],  # Para 0
            [0.95, 0.05, 0.0],  # Para 1 — similar to 0
            [0.0, 0.0, 1.0],  # Para 2 — very different
            [0.0, 0.05, 0.95],  # Para 3 — similar to 2
        ])

        chunker = SemanticChunker(embedder=mock_embedder)
        text = "Para zero.\n\nPara one.\n\nPara two.\n\nPara three."
        chunks = await chunker.chunk(text)
        assert len(chunks) >= 1
        mock_embedder.embed_texts.assert_called_once()
