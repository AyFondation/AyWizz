# =============================================================================
# File: deterministic.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/embedding/deterministic.py
# Description: Zero-dependency deterministic embedding adapter for the v1
#              baseline. Maps text → fixed-length float vector using hashed
#              token counts, normalised. Not semantically meaningful —
#              lexical overlap only — but reproducible across runs and
#              machines, fast, and requires no ML library.
#
#              Used by the default v1 deployment when sentence-transformers
#              extras are not installed, and by all unit/integration tests
#              so the suite stays hermetic.
#
# @relation implements:R-400-001
# @relation implements:R-400-003
# =============================================================================

from __future__ import annotations

import hashlib
import math
import re

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


class DeterministicHashEmbedder:
    """Reproducible bag-of-hashed-tokens embedder.

    For each lowercased token in the input, compute its sha256, take the
    first `ceil(log2(dimension))` bytes to derive a bucket in `[0, dim)`,
    increment that bucket, then L2-normalise the resulting vector.

    Two inputs with identical token sets (order-insensitive) produce
    identical vectors. Two inputs sharing N tokens out of M yield cosine
    similarity approximating N/M, which is enough to make retrieval tests
    meaningful.
    """

    def __init__(
        self,
        model_id: str = "deterministic-hash-v1",
        dimension: int = 128,
        max_input_tokens: int = 8192,
    ) -> None:
        if dimension < 8:
            raise ValueError("dimension SHALL be >= 8 for this adapter")
        self.model_id = model_id
        self.dimension = dimension
        self.max_input_tokens = max_input_tokens

    async def embed_one(self, text: str) -> list[float]:
        return self._embed_sync(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_sync(t) for t in texts]

    # ------------------------------------------------------------------
    # Pure helper — test-friendly sync path.
    # ------------------------------------------------------------------

    def _embed_sync(self, text: str) -> list[float]:
        tokens = _TOKEN_PATTERN.findall(text.lower())[: self.max_input_tokens]
        vector = [0.0] * self.dimension
        if not tokens:
            # L2-normalised zero vector is still a zero vector; give it a
            # nonzero fallback so cosine similarity is well-defined.
            vector[0] = 1.0
            return vector
        for tok in tokens:
            idx = self._bucket(tok)
            vector[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def _bucket(self, token: str) -> int:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        # Take first 8 bytes as a 64-bit unsigned int, modulo dimension.
        raw = int.from_bytes(digest[:8], "big", signed=False)
        return raw % self.dimension
