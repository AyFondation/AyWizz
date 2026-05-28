# =============================================================================
# File: chunker.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/ingestion/chunker.py
# Description: Fixed-window chunker (R-400-022). v1 uses whitespace-token
#              windows; structure-aware chunking per format is deferred to
#              v2 (Q-400-005).
#
# @relation implements:R-400-022
# =============================================================================

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_SPLIT = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One chunk produced by the chunker."""

    index: int
    text: str


def chunk_text(
    text: str,
    *,
    token_size: int = 512,
    overlap: int = 64,
) -> list[Chunk]:
    """Split text into fixed-size windows with overlap.

    Tokens are whitespace-delimited fragments — a conservative
    approximation of the embedding model's tokenizer that avoids pulling
    a heavyweight dependency just for chunking. Real tokenizer alignment
    is a v2 concern (Q-400-005).

    Guarantees:
    - Empty input returns an empty list (no zero-length chunk).
    - `overlap` SHALL be < `token_size`; otherwise ValueError.
    - Chunks are numbered from 0.
    """
    if token_size <= 0:
        raise ValueError("token_size must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= token_size:
        raise ValueError(
            f"overlap ({overlap}) must be strictly less than "
            f"token_size ({token_size})"
        )

    tokens = _TOKEN_SPLIT.findall(text)
    if not tokens:
        return []

    step = token_size - overlap
    chunks: list[Chunk] = []
    start = 0
    while start < len(tokens):
        window = tokens[start : start + token_size]
        if not window:
            break
        chunks.append(Chunk(index=len(chunks), text=" ".join(window)))
        if start + token_size >= len(tokens):
            break
        start += step
    return chunks
