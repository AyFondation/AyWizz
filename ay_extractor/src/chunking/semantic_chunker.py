# src/chunking/semantic_chunker.py — v1
"""Semantic chunking strategy using embedding similarity.

Splits text at points where semantic similarity between consecutive
segments drops below a threshold. Requires an embedding provider.
See spec §4.1 step 2a.
"""

from __future__ import annotations

import hashlib
import re

from ayextractor.chunking.base_chunker import BaseChunker
from ayextractor.config.settings import Settings
from ayextractor.core.models import Chunk, DocumentStructure

_ATOMIC_BLOCK = re.compile(
    r"<<<(?:IMAGE_CONTENT|TABLE_CONTENT)\b.*?<<<END_(?:IMAGE_CONTENT|TABLE_CONTENT)>>>",
    re.DOTALL,
)


class SemanticChunker(BaseChunker):
    """Chunk text using semantic similarity between consecutive segments.

    Falls back to structural chunking when no embedder is available.
    """

    def __init__(self, settings: Settings | None = None, embedder: object | None = None):
        self._target = 2000 if settings is None else settings.chunk_target_size
        self._embedder = embedder

    @property
    def strategy_name(self) -> str:
        return "semantic"

    async def chunk(
        self,
        text: str,
        structure: DocumentStructure | None = None,
        source_file: str = "unknown",
    ) -> list[Chunk]:
        """Split text into semantically coherent chunks.

        If no embedder is available, falls back to paragraph-based splitting.
        """
        if not text.strip():
            return []

        # Split into paragraphs first (respecting atomic blocks)
        paragraphs = self._split_paragraphs(text)

        if self._embedder is None or len(paragraphs) < 3:
            # Fallback: merge paragraphs by target size
            merged = self._merge_by_size(paragraphs)
        else:
            # Use embeddings to find optimal split points
            merged = await self._merge_by_similarity(paragraphs)

        # Build Chunk objects
        chunks: list[Chunk] = []
        offset = 0
        for i, content in enumerate(merged):
            chunk_id = f"chunk_{i:03d}"
            fp = hashlib.sha256(content.encode()).hexdigest()[:16]
            end_offset = offset + len(content.encode())

            chunk = Chunk(
                id=chunk_id,
                position=i,
                content=content,
                source_file=source_file,
                byte_offset_start=offset,
                byte_offset_end=end_offset,
                char_count=len(content),
                word_count=len(content.split()),
                token_count_est=len(content) // 4,
                fingerprint=fp,
            )
            chunks.append(chunk)
            offset = end_offset

        # Link chunks
        for i, chunk in enumerate(chunks):
            if i > 0:
                chunk.preceding_chunk_id = chunks[i - 1].id
            if i < len(chunks) - 1:
                chunk.following_chunk_id = chunks[i + 1].id

        return chunks

    def _split_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs, keeping atomic blocks intact."""
        segments: list[str] = []
        last_end = 0

        for match in _ATOMIC_BLOCK.finditer(text):
            before = text[last_end:match.start()]
            if before.strip():
                for p in re.split(r"\n{2,}", before):
                    if p.strip():
                        segments.append(p.strip())
            segments.append(match.group())
            last_end = match.end()

        remaining = text[last_end:]
        if remaining.strip():
            for p in re.split(r"\n{2,}", remaining):
                if p.strip():
                    segments.append(p.strip())

        return segments

    def _merge_by_size(self, paragraphs: list[str]) -> list[str]:
        """Simple merge by target size."""
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for para in paragraphs:
            if current_len + len(para) > self._target and current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            current.append(para)
            current_len += len(para)

        if current:
            chunks.append("\n\n".join(current))
        return chunks

    async def _merge_by_similarity(self, paragraphs: list[str]) -> list[str]:
        """Merge using embedding similarity to find split points."""
        # Compute embeddings for each paragraph
        embeddings = await self._embedder.embed_texts(paragraphs)  # type: ignore[union-attr]

        import numpy as np
        from ayextractor.core.similarity import cosine_similarity_matrix

        emb_matrix = np.array(embeddings)
        sim_matrix = cosine_similarity_matrix(emb_matrix)

        # Compute consecutive similarity
        consecutive_sim = [sim_matrix[i, i + 1] for i in range(len(paragraphs) - 1)]

        # Find split points: where similarity drops
        threshold = 0.5
        split_points: list[int] = []
        for i, sim in enumerate(consecutive_sim):
            if sim < threshold:
                split_points.append(i + 1)

        # Merge between split points
        chunks: list[str] = []
        start = 0
        for sp in split_points:
            chunk_paras = paragraphs[start:sp]
            if chunk_paras:
                chunks.append("\n\n".join(chunk_paras))
            start = sp
        # Last chunk
        if start < len(paragraphs):
            chunks.append("\n\n".join(paragraphs[start:]))

        return chunks or ["\n\n".join(paragraphs)]
