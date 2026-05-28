# =============================================================================
# File: test_chunker.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_chunker.py
# Description: Unit tests for the fixed-window chunker (R-400-022).
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c7_memory.ingestion.chunker import chunk_text


@pytest.mark.unit
class TestChunker:
    def test_empty_input_returns_empty(self) -> None:
        assert chunk_text("") == []

    def test_small_input_one_chunk(self) -> None:
        chunks = chunk_text("hello world and friends", token_size=10, overlap=2)
        assert len(chunks) == 1
        assert chunks[0].index == 0
        assert chunks[0].text == "hello world and friends"

    def test_long_input_multiple_chunks(self) -> None:
        text = " ".join(f"w{i}" for i in range(100))
        chunks = chunk_text(text, token_size=20, overlap=5)
        assert len(chunks) > 1
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_overlap_creates_shared_tokens_between_chunks(self) -> None:
        text = " ".join(f"t{i}" for i in range(30))
        chunks = chunk_text(text, token_size=10, overlap=3)
        # First chunk: tokens 0..9. Second chunk: 7..16 (step = 10-3 = 7).
        first = chunks[0].text.split()
        second = chunks[1].text.split()
        assert first[-3:] == second[:3]

    def test_overlap_equal_to_token_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            chunk_text("hello world", token_size=5, overlap=5)

    def test_negative_overlap_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            chunk_text("hello", token_size=5, overlap=-1)

    def test_zero_token_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            chunk_text("hello", token_size=0, overlap=0)
