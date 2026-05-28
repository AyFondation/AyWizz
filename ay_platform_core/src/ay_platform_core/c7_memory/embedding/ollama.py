# =============================================================================
# File: ollama.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/embedding/ollama.py
# Description: Ollama-backed embedder. Talks to an Ollama server's
#              `/api/embeddings` endpoint and returns the raw embedding
#              vector as `list[float]`. Suitable for local dev and tests
#              via the `ollama_container` fixture; production deployments
#              point the same class at a managed Ollama instance or any
#              Ollama-compatible embedding API.
#
#              The model_id / dimension are read from the Ollama server
#              at first call (via a one-shot probe) and cached for the
#              lifetime of the embedder instance, so callers don't have
#              to know the model's dimension up front.
#
# @relation implements:R-400-001
# @relation implements:R-400-002
# @relation implements:E-400-001
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ay_platform_core.observability import make_traced_client


class OllamaEmbedder:
    """Embedding adapter that calls Ollama's /api/embeddings endpoint.

    Conforms to the `EmbeddingProvider` Protocol in `embedding/base.py`:
    exposes ``model_id``, ``dimension``, ``max_input_tokens`` plus
    ``embed_one`` / ``embed_batch`` coroutines.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        max_input_tokens: int = 2048,
        request_timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model_id = model_id
        self.max_input_tokens = max_input_tokens
        # Dimension is discovered lazily on first call; -1 signals
        # "not probed yet". The `dimension` attribute SHALL be replaced
        # by an integer before any cross-service call depending on it.
        self.dimension: int = -1
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or make_traced_client(
            base_url=self._base_url, timeout=request_timeout_s
        )
        self._probe_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _probe_dimension(self) -> int:
        """One-shot call to learn the model's output dimension."""
        async with self._probe_lock:
            if self.dimension >= 0:
                return self.dimension
            vec = await self._single_embed("dimension-probe")
            self.dimension = len(vec)
            return self.dimension

    async def _single_embed(self, text: str) -> list[float]:
        resp = await self._client.post(
            "/api/embeddings",
            json={"model": self.model_id, "prompt": text},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Ollama /api/embeddings failed: HTTP {resp.status_code} {resp.text}"
            )
        body: dict[str, Any] = resp.json()
        vec = body.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise RuntimeError(
                f"Ollama returned no embedding vector: {body!r}"
            )
        return [float(x) for x in vec]

    async def embed_one(self, text: str) -> list[float]:
        if self.dimension < 0:
            await self._probe_dimension()
        return await self._single_embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Ollama's endpoint is single-prompt; the batch call is a
        sequential gather. Callers that need high throughput should run
        multiple workers rather than expect fan-out here."""
        if self.dimension < 0:
            await self._probe_dimension()
        results: list[list[float]] = []
        for text in texts:
            results.append(await self._single_embed(text))
        return results
