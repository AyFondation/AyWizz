# src/core/similarity.py — v2
"""GPU-aware cosine similarity utility.

Selects backend based on GPU_SIMILARITY_BACKEND env var:
- sklearn (CPU, default — falls back to numpy if sklearn not installed)
- numpy (CPU, pure numpy fallback)
- cupy (GPU, requires cupy-cuda12x)
- torch (GPU, requires torch with CUDA)

See spec §33.5 for details.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Lazy-resolved backend setting to avoid circular imports with config/settings.py.
_backend: str | None = None


def _get_backend() -> str:
    """Resolve GPU_SIMILARITY_BACKEND from env (lazy, cached)."""
    global _backend
    if _backend is None:
        import os

        _backend = os.environ.get("GPU_SIMILARITY_BACKEND", "sklearn")
    return _backend


def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Compute pairwise cosine similarity matrix.

    Args:
        embeddings: 2D array of shape (n_samples, n_features).

    Returns:
        Similarity matrix of shape (n_samples, n_samples) with values in [-1, 1].

    Raises:
        ValueError: If embeddings is not a 2D array or is empty.
    """
    if embeddings.ndim != 2:
        raise ValueError(f"Expected 2D array, got {embeddings.ndim}D")
    if embeddings.shape[0] == 0:
        return np.empty((0, 0), dtype=np.float64)

    backend = _get_backend()

    if backend == "cupy":
        return _cosine_cupy(embeddings)
    elif backend == "torch":
        return _cosine_torch(embeddings)
    elif backend == "numpy":
        return _cosine_numpy(embeddings)
    else:
        return _cosine_sklearn(embeddings)


def _cosine_numpy(embeddings: np.ndarray) -> np.ndarray:
    """Pure numpy cosine similarity (no external deps)."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-10)
    normalized = embeddings / norms
    return normalized @ normalized.T


def _cosine_sklearn(embeddings: np.ndarray) -> np.ndarray:
    """CPU path via sklearn, falls back to numpy if unavailable."""
    try:
        from sklearn.metrics.pairwise import cosine_similarity

        return cosine_similarity(embeddings)
    except ImportError:
        logger.debug("sklearn not available, using numpy fallback")
        return _cosine_numpy(embeddings)


def _cosine_cupy(embeddings: np.ndarray) -> np.ndarray:
    """GPU path via cupy."""
    try:
        import cupy as cp

        gpu_emb = cp.asarray(embeddings)
        norms = cp.linalg.norm(gpu_emb, axis=1, keepdims=True)
        # Avoid division by zero
        norms = cp.maximum(norms, 1e-10)
        result = (gpu_emb @ gpu_emb.T) / (norms @ norms.T)
        return cp.asnumpy(result)
    except ImportError:
        logger.warning("cupy not available, falling back to numpy")
        return _cosine_numpy(embeddings)


def _cosine_torch(embeddings: np.ndarray) -> np.ndarray:
    """GPU path via PyTorch."""
    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            logger.warning("CUDA not available for torch, running on CPU")
        t = torch.tensor(embeddings, dtype=torch.float32, device=device)
        # Normalize then matrix multiply for efficiency
        t_norm = torch.nn.functional.normalize(t, p=2, dim=1)
        result = (t_norm @ t_norm.T).cpu().numpy()
        return result
    except ImportError:
        logger.warning("torch not available, falling back to numpy")
        return _cosine_numpy(embeddings)


def reset_backend() -> None:
    """Reset cached backend (for testing)."""
    global _backend
    _backend = None