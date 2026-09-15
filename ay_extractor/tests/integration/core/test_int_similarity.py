# tests/integration/core/test_int_similarity.py — v2
"""Integration tests for core/similarity.py — GPU-aware cosine similarity.

Covers: cosine_similarity_matrix, backend selection, reset_backend.
No Docker required.

Source API:
- cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray
  Input: 2D array (n_samples, n_features)
  Output: (n_samples, n_samples) similarity matrix in [-1, 1]
- reset_backend() → clears cached backend for testing
"""

from __future__ import annotations

import os

import numpy as np
import pytest


class TestCosineSimilarityMatrix:

    def test_identical_vectors(self):
        """Identical vectors → similarity 1.0."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        emb = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        sim = cosine_similarity_matrix(emb)
        assert sim.shape == (2, 2)
        assert abs(sim[0, 1] - 1.0) < 1e-6
        assert abs(sim[1, 0] - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """Orthogonal vectors → similarity 0.0."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        emb = np.array([[1.0, 0.0], [0.0, 1.0]])
        sim = cosine_similarity_matrix(emb)
        assert abs(sim[0, 1]) < 1e-6

    def test_opposite_vectors(self):
        """Opposite vectors → similarity -1.0."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        emb = np.array([[1.0, 0.0], [-1.0, 0.0]])
        sim = cosine_similarity_matrix(emb)
        assert sim[0, 1] < -0.99

    def test_diagonal_is_one(self):
        """Self-similarity on diagonal should be 1.0."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        rng = np.random.default_rng(42)
        emb = rng.standard_normal((5, 128))
        sim = cosine_similarity_matrix(emb)
        for i in range(5):
            assert abs(sim[i, i] - 1.0) < 1e-5

    def test_symmetry(self):
        """Similarity matrix should be symmetric."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        rng = np.random.default_rng(42)
        emb = rng.standard_normal((4, 64))
        sim = cosine_similarity_matrix(emb)
        np.testing.assert_array_almost_equal(sim, sim.T)

    def test_single_vector(self):
        """Single vector → 1x1 matrix with self-similarity 1.0."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        emb = np.array([[3.0, 4.0, 0.0]])
        sim = cosine_similarity_matrix(emb)
        assert sim.shape == (1, 1)
        assert abs(sim[0, 0] - 1.0) < 1e-6

    def test_empty_array(self):
        """Empty embeddings → empty matrix."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        emb = np.empty((0, 128))
        sim = cosine_similarity_matrix(emb)
        assert sim.shape == (0, 0)

    def test_raises_on_1d(self):
        """1D input should raise ValueError."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        with pytest.raises(ValueError, match="2D"):
            cosine_similarity_matrix(np.array([1.0, 2.0, 3.0]))

    def test_values_in_range(self):
        """All values should be in [-1, 1]."""
        from ayextractor.core.similarity import cosine_similarity_matrix
        rng = np.random.default_rng(99)
        emb = rng.standard_normal((10, 32))
        sim = cosine_similarity_matrix(emb)
        assert np.all(sim >= -1.0 - 1e-6)
        assert np.all(sim <= 1.0 + 1e-6)


class TestBackendSelection:

    def test_numpy_backend(self):
        """Force numpy backend and verify correct results."""
        from ayextractor.core.similarity import (
            cosine_similarity_matrix,
            reset_backend,
        )
        os.environ["GPU_SIMILARITY_BACKEND"] = "numpy"
        reset_backend()
        emb = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
        sim = cosine_similarity_matrix(emb)
        assert abs(sim[0, 2] - 1.0) < 1e-6
        assert abs(sim[0, 1]) < 1e-6
        # Cleanup
        os.environ.pop("GPU_SIMILARITY_BACKEND", None)
        reset_backend()

    def test_default_backend_works(self):
        """Default (sklearn) backend produces valid results."""
        from ayextractor.core.similarity import (
            cosine_similarity_matrix,
            reset_backend,
        )
        os.environ.pop("GPU_SIMILARITY_BACKEND", None)
        reset_backend()
        emb = np.eye(3)
        sim = cosine_similarity_matrix(emb)
        # Identity vectors: diagonal=1, off-diagonal=0
        np.testing.assert_array_almost_equal(np.diag(sim), [1.0, 1.0, 1.0])
        for i in range(3):
            for j in range(3):
                if i != j:
                    assert abs(sim[i, j]) < 1e-5