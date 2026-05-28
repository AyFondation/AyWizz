# =============================================================================
# File: fusion.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/retrieval/fusion.py
# Description: Reciprocal Rank Fusion (RRF) for hybrid retrieval (R-400-202).
#              Merges several ranked lists (dense cosine + BM25 lexical) into
#              one score per item without tuning a per-arm weight : an item's
#              fused score is the sum over arms of 1 / (k + rank), rank being
#              its 1-based position in that arm. Higher fused score = better.
#              Pure + deterministic — no I/O.
# =============================================================================

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]], *, k: int = 60
) -> dict[str, float]:
    """Fuse ranked id-lists by RRF. Each list is assumed already ordered
    best-first. An id present in several lists accumulates a contribution
    from each. `k` damps the weight of low ranks (60 is the common default).
    Returns {id: fused_score} (unsorted — the caller orders)."""
    if k < 1:
        raise ValueError("k must be >= 1")
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores
