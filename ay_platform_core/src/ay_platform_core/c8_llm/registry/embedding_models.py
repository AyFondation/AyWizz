# =============================================================================
# File: embedding_models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_models.py
# Description: Pydantic contracts for the platform EMBEDDING registry (D-011,
#              parallel to the chat model registry). An embedding is a
#              provider-independent, in-app resource: the root operator
#              registers embedding PROVIDERS (endpoint + adapter + encrypted
#              key) and MODELS (upstream name + DIMENSION), the tenant makes
#              them available per project + a default, and C7 resolves the
#              embedder PER PROJECT (never per call — every vector of an index
#              MUST share one model + dimension; changing it re-indexes).
#
#              Unlike the chat model, an embedding carries NO quality tier,
#              capabilities or per-token cost — its defining attribute is the
#              output `dimension` (the hard index-consistency constraint,
#              R-400-226). Keys live on the PROVIDER (write-only, encrypted);
#              this layer never serialises a secret (use `to_public()`).
#
# @relation implements:R-400-226
# =============================================================================

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingAdapter(StrEnum):
    """How C7 calls the provider to embed text.

    - `ollama`             : POST `{base_url}/api/embeddings` (local/self-hosted).
    - `openai`             : POST `{base_url}/embeddings` (OpenAI-compatible).
    - `deterministic-hash` : in-process, NO endpoint/key — for dev/tests only.
    """

    OLLAMA = "ollama"
    OPENAI = "openai"
    DETERMINISTIC_HASH = "deterministic-hash"


# ---------------------------------------------------------------------------
# Provider (endpoint + credential)
# ---------------------------------------------------------------------------


class _EmbeddingProviderFields(BaseModel):
    """Descriptive provider fields (no secret)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    """Human, MUTABLE name; unique among embedding providers."""
    adapter: EmbeddingAdapter
    base_url: str = Field(default="", max_length=500)
    """Endpoint the adapter POSTs to. EMPTY only for `deterministic-hash`."""


class EmbeddingProviderUpsert(_EmbeddingProviderFields):
    """PUT body for provider metadata (no key)."""


class EmbeddingProviderEntry(_EmbeddingProviderFields):
    """The STORED document (Arango `_key = provider_id`). `api_key_ciphertext`
    is a SecretCipher token (AAD `embedding_provider:<provider_id>:api_key`) or
    None. NEVER serialised into an HTTP response — use `to_public()`."""

    provider_id: str = Field(min_length=1)
    api_key_ciphertext: str | None = None
    api_key_hint: str = ""
    effective_from: str

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.provider_id
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> EmbeddingProviderEntry:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)

    def to_public(self) -> EmbeddingProviderPublic:
        return EmbeddingProviderPublic(
            provider_id=self.provider_id,
            name=self.name,
            adapter=self.adapter,
            base_url=self.base_url,
            effective_from=self.effective_from,
            key_status="set" if self.api_key_ciphertext else "not_set",
            api_key_hint=self.api_key_hint,
        )


class EmbeddingProviderPublic(_EmbeddingProviderFields):
    """API READ projection — no ciphertext, only a non-reversible key status."""

    provider_id: str
    effective_from: str
    key_status: Literal["set", "not_set"]
    api_key_hint: str = ""


class EmbeddingProviderApiKeyUpdate(BaseModel):
    """PUT body of the dedicated write-only provider-key endpoint."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, repr=False)


class EmbeddingProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[EmbeddingProviderPublic]


# ---------------------------------------------------------------------------
# Model (upstream name + DIMENSION)
# ---------------------------------------------------------------------------


class _EmbeddingModelFields(BaseModel):
    """Shared descriptive fields of an embedding model. No secret here — the
    key + endpoint live on the referenced provider."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=120)
    """Human, MUTABLE name; unique among embedding models. Display + selection —
    NOT a stable reference key (that is `model_id`)."""
    provider_id: str = Field(min_length=1)
    """Stable id of the embedding provider (endpoint + credential)."""
    upstream_model: str = Field(min_length=1, max_length=200)
    """The model id AT the provider, e.g. `all-minilm` or `text-embedding-3-small`."""
    dimension: int = Field(ge=1, le=65536)
    """Output vector size. The HARD index-consistency axis (R-400-226): all
    vectors of an index share it; changing it requires re-indexing."""
    enabled: bool = True


class EmbeddingModelUpsert(_EmbeddingModelFields):
    """Write body for a model's metadata. On create the service mints the
    `model_id`; on update the `model_id` is the path key and every field here
    may change (but a `dimension` change on an in-use model is guarded)."""


class EmbeddingModelEntry(_EmbeddingModelFields):
    """The STORED document (Arango `_key = model_id`)."""

    model_id: str = Field(min_length=1)
    effective_from: str
    """ISO-8601 UTC timestamp of the last write (effective-dated)."""

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.model_id
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> EmbeddingModelEntry:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)

    def to_public(self) -> EmbeddingModelPublic:
        return EmbeddingModelPublic(
            model_id=self.model_id,
            alias=self.alias,
            provider_id=self.provider_id,
            upstream_model=self.upstream_model,
            dimension=self.dimension,
            enabled=self.enabled,
            effective_from=self.effective_from,
        )


class EmbeddingModelPublic(_EmbeddingModelFields):
    """API READ projection (no secret — the key is on the provider)."""

    model_id: str
    effective_from: str


class EmbeddingModelListResponse(BaseModel):
    """GET response — the full embedding-model registry as public projections."""

    model_config = ConfigDict(extra="forbid")

    models: list[EmbeddingModelPublic]
