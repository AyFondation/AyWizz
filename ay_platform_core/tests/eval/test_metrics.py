# =============================================================================
# File: test_metrics.py
# Version: 1
# Path: ay_platform_core/tests/eval/test_metrics.py
# Description: Unit tests for the retrieval-eval metrics (D-017 T2 slice).
# =============================================================================

from __future__ import annotations

import pytest

from tests.eval.metrics import (
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

pytestmark = pytest.mark.unit


class TestRecall:
    def test_all_relevant_in_top_k(self) -> None:
        assert recall_at_k(["a", "b", "c"], {"a", "b"}, 3) == 1.0

    def test_partial(self) -> None:
        assert recall_at_k(["a", "x", "y"], {"a", "b"}, 3) == 0.5

    def test_k_truncates(self) -> None:
        assert recall_at_k(["x", "a"], {"a"}, 1) == 0.0  # a is below k=1

    def test_no_relevant_is_zero(self) -> None:
        assert recall_at_k(["a"], set(), 3) == 0.0


class TestPrecision:
    def test_basic(self) -> None:
        assert precision_at_k(["a", "x", "b"], {"a", "b"}, 3) == pytest.approx(2 / 3)

    def test_empty_or_zero_k(self) -> None:
        assert precision_at_k([], {"a"}, 3) == 0.0
        assert precision_at_k(["a"], {"a"}, 0) == 0.0


class TestReciprocalRank:
    def test_first_relevant_position(self) -> None:
        assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)

    def test_none_found(self) -> None:
        assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


class TestNDCG:
    def test_perfect_ranking_is_one(self) -> None:
        assert ndcg_at_k(["a", "b", "x"], {"a", "b"}, 3) == pytest.approx(1.0)

    def test_relevant_lower_ranks_below_one(self) -> None:
        # one relevant item at rank 3 → dcg = 1/log2(4) ; idcg = 1/log2(2)=1
        score = ndcg_at_k(["x", "y", "a"], {"a"}, 3)
        assert 0.0 < score < 1.0
        assert score == pytest.approx(1.0 / 2.0)  # log2(4)=2

    def test_no_relevant_is_zero(self) -> None:
        assert ndcg_at_k(["a"], set(), 3) == 0.0


def test_mean() -> None:
    assert mean([0.0, 1.0, 0.5]) == pytest.approx(0.5)
    assert mean([]) == 0.0
