# =============================================================================
# File: test_fusion.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_fusion.py
# Description: Unit tests for Reciprocal Rank Fusion (R-400-202).
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c7_memory.retrieval.fusion import reciprocal_rank_fusion

pytestmark = pytest.mark.unit


class TestReciprocalRankFusion:
    def test_item_in_both_arms_outranks_item_in_one(self) -> None:
        # "b" is rank-2 in both arms ; "a" is rank-1 in only one. With k=60,
        # b's two contributions (1/62 + 1/62) beat a's single 1/61.
        dense = ["a", "b", "c"]
        lexical = ["d", "b", "e"]
        scores = reciprocal_rank_fusion([dense, lexical], k=60)
        assert scores["b"] > scores["a"]
        assert scores["b"] > scores["d"]

    def test_single_arm_ranks_by_position(self) -> None:
        scores = reciprocal_rank_fusion([["x", "y", "z"]], k=60)
        assert scores["x"] > scores["y"] > scores["z"]

    def test_exact_values(self) -> None:
        scores = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=60)
        # both at ranks 1 and 2 across the two arms → identical fused score.
        assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
        assert scores["b"] == pytest.approx(1 / 61 + 1 / 62)

    def test_empty_lists_yield_empty(self) -> None:
        assert reciprocal_rank_fusion([]) == {}
        assert reciprocal_rank_fusion([[], []]) == {}

    def test_invalid_k_rejected(self) -> None:
        with pytest.raises(ValueError, match="k must be"):
            reciprocal_rank_fusion([["a"]], k=0)
