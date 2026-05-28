# =============================================================================
# File: similarity.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/retrieval/similarity.py
# Description: Pure cosine similarity helpers used by the retriever.
#              Implemented in Python over Python floats — no NumPy — to
#              keep v1 dependency-light. NumPy-accelerated path can be
#              swapped in behind the same signatures once the corpus
#              size warrants it.
#
# @relation implements:R-400-011
# =============================================================================

from __future__ import annotations

import math


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length float vectors.

    Returns 0.0 when either vector has zero magnitude (instead of NaN) so
    callers can sort without special-casing. Raises ValueError on length
    mismatch — embeddings from different models SHALL NOT be compared
    (R-400-002 enforces this upstream; this is a defence-in-depth check).
    """
    if len(a) != len(b):
        raise ValueError(
            f"cosine: vectors have different lengths ({len(a)} vs {len(b)})"
        )
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def snippet(text: str, *, max_chars: int = 240) -> str:
    """Truncate `text` to `max_chars` on a whitespace boundary, ellipsised."""
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind(" ", 0, max_chars)
    if cutoff < max_chars // 2:  # pathological long token
        cutoff = max_chars
    return text[:cutoff].rstrip() + "…"
