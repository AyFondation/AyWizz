# =============================================================================
# File: test_similarity.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_similarity.py
# Description: Unit tests for the cosine + snippet helpers.
# =============================================================================

from __future__ import annotations

import math

import pytest

from ay_platform_core.c7_memory.retrieval.similarity import cosine, snippet


@pytest.mark.unit
class TestCosine:
    def test_identical_vectors_score_one(self) -> None:
        v = [1.0, 2.0, 3.0]
        assert abs(cosine(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_zero_vector_returns_zero(self) -> None:
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="lengths"):
            cosine([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_opposite_direction_scores_minus_one(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine(a, b) + 1.0) < 1e-9

    def test_numerical_stability(self) -> None:
        a = [0.5, 0.5, 0.5, 0.5]
        b = [0.5, 0.5, 0.5, 0.5]
        result = cosine(a, b)
        assert not math.isnan(result)
        assert abs(result - 1.0) < 1e-9


@pytest.mark.unit
class TestSnippet:
    def test_short_text_unchanged(self) -> None:
        assert snippet("hello world", max_chars=240) == "hello world"

    def test_truncated_on_whitespace(self) -> None:
        text = "word " * 100  # 500 chars
        result = snippet(text, max_chars=50)
        assert result.endswith("…")
        assert len(result) <= 51  # 50 + ellipsis

    def test_pathological_long_token(self) -> None:
        text = "a" * 500
        result = snippet(text, max_chars=50)
        assert result.endswith("…")
        assert len(result) <= 51
