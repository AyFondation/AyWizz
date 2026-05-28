# =============================================================================
# File: test_embedder.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_embedder.py
# Description: Unit tests for the deterministic hash embedder — reproducibility,
#              normalisation, lexical-overlap similarity property.
# =============================================================================

from __future__ import annotations

import math

import pytest

from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.retrieval.similarity import cosine


@pytest.mark.unit
class TestDeterministicEmbedder:
    def test_declared_metadata(self) -> None:
        e = DeterministicHashEmbedder(dimension=128)
        assert e.model_id == "deterministic-hash-v1"
        assert e.dimension == 128

    def test_vector_is_unit_length(self) -> None:
        e = DeterministicHashEmbedder(dimension=64)
        vector = e._embed_sync("the quick brown fox")
        norm = math.sqrt(sum(v * v for v in vector))
        assert abs(norm - 1.0) < 1e-9

    def test_reproducible(self) -> None:
        e = DeterministicHashEmbedder(dimension=64)
        a = e._embed_sync("hello world")
        b = e._embed_sync("hello world")
        assert a == b

    def test_token_order_insensitive(self) -> None:
        e = DeterministicHashEmbedder(dimension=64)
        a = e._embed_sync("quick brown fox")
        b = e._embed_sync("fox brown quick")
        assert a == b

    def test_similarity_reflects_lexical_overlap(self) -> None:
        e = DeterministicHashEmbedder(dimension=256)
        q = e._embed_sync("a widget that frobulates the gadget")
        match = e._embed_sync("a widget that frobulates the gadget very well")
        unrelated = e._embed_sync("underwater photography in polar seas")
        assert cosine(q, match) > cosine(q, unrelated)

    def test_empty_input_returns_nonzero_vector(self) -> None:
        e = DeterministicHashEmbedder(dimension=16)
        vector = e._embed_sync("")
        assert sum(abs(v) for v in vector) > 0

    def test_dimension_below_min_rejected(self) -> None:
        with pytest.raises(ValueError, match=">= 8"):
            DeterministicHashEmbedder(dimension=4)

    @pytest.mark.asyncio
    async def test_embed_one_and_batch_agree(self) -> None:
        e = DeterministicHashEmbedder(dimension=32)
        one = await e.embed_one("hello world")
        batch = await e.embed_batch(["hello world"])
        assert batch == [one]
