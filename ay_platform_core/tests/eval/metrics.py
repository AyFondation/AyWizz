# =============================================================================
# File: metrics.py
# Version: 1
# Path: ay_platform_core/tests/eval/metrics.py
# Description: Pure information-retrieval metrics for the retrieval-quality
#              eval harness (D-017 T2 reference-based slice / Q-400-011).
#              Binary relevance over an ordered list of retrieved ids vs a
#              set of relevant ids. No I/O — unit-tested independently of the
#              harness runner.
# =============================================================================

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the relevant items present in the top-k. 0.0 when there
    are no relevant items (undefined → 0 by convention)."""
    if not relevant or k <= 0:
        return 0.0
    hits = sum(1 for item in retrieved[:k] if item in relevant)
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k that is relevant."""
    if k <= 0:
        return 0.0
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for item in top if item in relevant) / len(top)


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """1 / rank of the first relevant item (0.0 if none retrieved)."""
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Normalised discounted cumulative gain @k, binary relevance. The ideal
    DCG places min(|relevant|, k) hits at the top."""
    if not relevant or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(retrieved[:k], start=1)
        if item in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean (0.0 for an empty sequence)."""
    return sum(values) / len(values) if values else 0.0
