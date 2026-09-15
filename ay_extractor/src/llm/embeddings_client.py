# src/llm/embeddings_client.py — v1
"""Embeddings client — OpenAI-compatible `/embeddings` endpoint.

D-020 v2 §B1: embeddings are now computed by C13 (AyExtractor), not by
C7 downstream. C13 writes `02_chunks/embeddings.jsonl` next to
`chunks.jsonl` per R-400-222 v2 so the artifact set is byte-exact
reproducible (R-400-207 reproducible-rebuild mandate). The run manifest
stamps `embedding_model` + `embedding_model_version` + `embedding_dimension`
per R-400-221 v2.

In-cluster: this client points at C8 LiteLLM via `base_url`, which
exposes a unified `/embeddings` surface across providers (Voyage,
OpenAI, sentence-transformers, …). Standalone dev: set `base_url` to any
OpenAI-API-compatible embeddings endpoint.

Symmetric design with `llm/adapters/openai_adapter.py` — same wire
protocol, just a different endpoint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmbeddingBatchResult:
    """Result of a batch `/embeddings` invocation.

    Attributes mirror the OpenAI `/v1/embeddings` response shape so the
    caller can persist them faithfully to `embeddings.jsonl` and to the
    run manifest (R-400-221 v2).
    """

    vectors: list[list[float]]
    model: str            # = model_id reported by the upstream (R-400-221)
    model_version: str    # = version pin if returned, "" otherwise
    dimension: int        # = len(vectors[0]) — same across the batch
    total_tokens: int
    latency_ms: int


class EmbeddingsClient:
    """Lightweight OpenAI-API-compat embeddings client.

    Usage:

        client = EmbeddingsClient(
            model="voyage-3",
            base_url="http://c8:8000/v1",
            api_key="$C8_GATEWAY_API_KEY",
        )
        result = await client.embed_batch(["chunk 1 text", "chunk 2 text", …])
        # result.vectors[i] is the embedding for input i
    """

    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str | None = None,
        batch_size: int = 100,
    ) -> None:
        """Initialise the client.

        Args:
            model: Embedding model id resolved by C8 (or the upstream
                endpoint when standalone). Examples: ``voyage-3``,
                ``text-embedding-3-small``.
            api_key: Bearer key. In-cluster: `C8_GATEWAY_API_KEY`.
            base_url: OpenAI-API endpoint. None → openai SDK defaults
                (reads `OPENAI_BASE_URL` env or hits `api.openai.com`).
                In-cluster: `http://c8:8000/v1`.
            batch_size: Max inputs per provider call. Most providers cap
                this at 100-2048; 100 is a safe default with low memory
                pressure.
        """
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._batch_size = batch_size

    def _client(self) -> Any:
        """Lazy openai SDK client (extras = `llm`)."""
        import openai

        return openai.AsyncOpenAI(
            api_key=self._api_key or None,
            base_url=self._base_url,
        )

    async def embed_batch(self, texts: list[str]) -> EmbeddingBatchResult:
        """Embed a list of texts in one or more provider calls.

        Long lists are split into `batch_size` sub-batches and concatenated.
        Token counts + latency are summed across sub-batches; model id is
        taken from the first response and re-checked across sub-batches
        for consistency (mismatch → ValueError, since R-400-221 v2 requires
        a single embedding_model per run).

        Args:
            texts: Inputs to embed. Empty strings ARE permitted (provider
                returns the zero / mean vector — caller responsibility to
                filter pre-call if undesired).

        Returns:
            EmbeddingBatchResult with len(vectors) == len(texts).

        Raises:
            ValueError: If sub-batches return different model ids.
            RuntimeError: On provider-side error (re-raised).
        """
        if not texts:
            return EmbeddingBatchResult(
                vectors=[],
                model=self._model,
                model_version="",
                dimension=0,
                total_tokens=0,
                latency_ms=0,
            )

        client = self._client()
        all_vectors: list[list[float]] = []
        total_tokens = 0
        total_latency = 0
        upstream_model: str | None = None

        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            t0 = time.monotonic()
            resp = await client.embeddings.create(model=self._model, input=batch)
            total_latency += int((time.monotonic() - t0) * 1000)

            # OpenAI SDK ≥ 1.0 returns a CreateEmbeddingResponse with .data,
            # .model, .usage. Each .data[i].embedding is the vector.
            if upstream_model is None:
                upstream_model = resp.model
            elif resp.model != upstream_model:
                raise ValueError(
                    "Embedding model id changed mid-batch: "
                    f"{upstream_model!r} → {resp.model!r}. "
                    "C13 SHALL use a single embedding model per run "
                    "(R-400-221 v2)."
                )

            all_vectors.extend(item.embedding for item in resp.data)
            if resp.usage is not None:
                total_tokens += resp.usage.total_tokens

        dimension = len(all_vectors[0]) if all_vectors else 0
        return EmbeddingBatchResult(
            vectors=all_vectors,
            model=upstream_model or self._model,
            model_version="",  # Upstream providers rarely return a pin via /embeddings
            dimension=dimension,
            total_tokens=total_tokens,
            latency_ms=total_latency,
        )

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text. Returns the raw vector."""
        result = await self.embed_batch([text])
        return result.vectors[0]
