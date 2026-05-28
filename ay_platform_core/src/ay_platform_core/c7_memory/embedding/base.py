# =============================================================================
# File: base.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/embedding/base.py
# Description: EmbeddingProvider protocol (E-400-001). All embedding adapters
#              — local sentence-transformers, hosted API, deterministic hash
#              for tests — satisfy this contract.
#
# @relation implements:E-400-001
# @relation implements:R-400-001
# @relation implements:R-400-002
# =============================================================================

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Adapter contract for embedding text.

    Implementations expose metadata (`model_id`, `dimension`,
    `max_input_tokens`) plus `embed_one` / `embed_batch` async methods.
    `model_id` SHALL be stable across restarts so stored embeddings can be
    correlated with the producing adapter (R-400-002).
    """

    model_id: str
    dimension: int
    max_input_tokens: int

    async def embed_one(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
