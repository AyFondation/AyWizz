# tests/unit/core/test_similarity.py — v1
"""Tests for core/similarity.py — GPU-aware cosine similarity."""

from __future__ import annotations

import numpy as np
import pytest

from ayextractor.core.similarity import cosine_similarity_matrix, reset_backend


@pytest.fixture(autouse=True)
def _reset():
    """Reset cached backend between tests."""
    reset_backend()
    yield
    reset_backend()


class TestCosineSimilarityMatrix:
    def test_identical_vectors(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        vecs = np.array([[1.0, 0.0], [1.0, 0.0]])
        result = cosine_similarity_matrix(vecs)
        assert result.shape == (2, 2)
        assert pytest.approx(result[0, 1], abs=1e-6) == 1.0

    def test_orthogonal_vectors(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        vecs = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = cosine_similarity_matrix(vecs)
        assert pytest.approx(result[0, 1], abs=1e-6) == 0.0

    def test_opposite_vectors(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        vecs = np.array([[1.0, 0.0], [-1.0, 0.0]])
        result = cosine_similarity_matrix(vecs)
        assert pytest.approx(result[0, 1], abs=1e-6) == -1.0

    def test_single_vector(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        vecs = np.array([[1.0, 2.0, 3.0]])
        result = cosine_similarity_matrix(vecs)
        assert result.shape == (1, 1)
        assert pytest.approx(result[0, 0], abs=1e-6) == 1.0

    def test_empty_array(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        vecs = np.empty((0, 3))
        result = cosine_similarity_matrix(vecs)
        assert result.shape == (0, 0)

    def test_invalid_dimensions(self):
        with pytest.raises(ValueError, match="Expected 2D"):
            cosine_similarity_matrix(np.array([1.0, 2.0, 3.0]))

    def test_default_backend_is_sklearn(self, monkeypatch):
        monkeypatch.delenv("GPU_SIMILARITY_BACKEND", raising=False)
        vecs = np.array([[1.0, 0.0], [0.0, 1.0]])
        result = cosine_similarity_matrix(vecs)
        assert result.shape == (2, 2)

    def test_symmetry(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        rng = np.random.default_rng(42)
        vecs = rng.random((5, 10))
        result = cosine_similarity_matrix(vecs)
        np.testing.assert_allclose(result, result.T, atol=1e-10)

    def test_diagonal_is_one(self, monkeypatch):
        monkeypatch.setenv("GPU_SIMILARITY_BACKEND", "sklearn")
        rng = np.random.default_rng(42)
        vecs = rng.random((4, 8))
        result = cosine_similarity_matrix(vecs)
        np.testing.assert_allclose(np.diag(result), 1.0, atol=1e-10)
