# =============================================================================
# File: openai.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/embedding/openai.py
# Description: OpenAI-compatible embedder (D-011). Talks to any endpoint that
#              implements the OpenAI `POST {base_url}/embeddings` contract
#              (OpenAI, Voyage-compat, vLLM, LiteLLM, Ollama's OpenAI shim, …)
#              with a Bearer api_key. Unlike the Ollama adapter, the dimension
#              is DECLARED by the registry model (a hosted model has a fixed
#              output dimension) and validated on every call — a mismatch is a
#              misconfiguration (R-400-222 index dimension consistency).
#
# @relation implements:R-400-001
# @relation implements:R-400-002
# @relation implements:E-400-001
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx

from ay_platform_core.observability import make_traced_client


class OpenAIEmbedder:
    """Embedding adapter for the OpenAI `/embeddings` wire format.

    Conforms to the `EmbeddingProvider` Protocol (`embedding/base.py`):
    exposes ``model_id``, ``dimension``, ``max_input_tokens`` plus
    ``embed_one`` / ``embed_batch`` coroutines. The Bearer key is bound to the
    client at construction (the registry decrypts it per resolution).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key: str,
        dimension: int,
        max_input_tokens: int = 8192,
        request_timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model_id = model_id
        # DECLARED by the registry (validated against the API response).
        self.dimension = dimension
        self.max_input_tokens = max_input_tokens
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or make_traced_client(
            base_url=self._base_url,
            timeout=request_timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        resp = await self._client.post(
            "/embeddings",
            json={"model": self.model_id, "input": inputs},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenAI /embeddings failed: HTTP {resp.status_code} {resp.text}"
            )
        body: dict[str, Any] = resp.json()
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(inputs):
            got = len(data) if isinstance(data, list) else "none"
            raise RuntimeError(
                f"OpenAI returned {got} embeddings for {len(inputs)} input(s)"
            )
        # The API SHOULD preserve input order; sort by `index` defensively.
        ordered = sorted(data, key=lambda d: int(d.get("index", 0)))
        vectors = [[float(x) for x in d["embedding"]] for d in ordered]
        for vec in vectors:
            if len(vec) != self.dimension:
                raise RuntimeError(
                    f"OpenAI embedding dimension {len(vec)} != declared "
                    f"{self.dimension} for model {self.model_id!r} — fix the "
                    f"registry model's dimension"
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        return (await self._embed([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed(texts)
