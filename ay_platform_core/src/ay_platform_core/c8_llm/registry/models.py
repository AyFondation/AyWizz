# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/models.py
# Description: Pydantic contracts for the platform LLM model registry.
#
#              v2 (provider normalisation + stable ids): a model is keyed by a
#              STABLE technical id (`model_id`); its `alias` and every other
#              attribute are MUTABLE without breaking tenant/project references
#              (which point at `model_id`). The endpoint URL + API key no longer
#              live on the model — they belong to the referenced PROVIDER
#              (`provider_id`, see provider_models.py). So this layer carries NO
#              secret at all; the write-only-key discipline now lives entirely
#              on the provider.
#
#              `model_quality` (low/medium/high) is the axis the tenant/project
#              selects — which model does the work. Distinct from the
#              orthogonal EnrichmentConfig `quality_tier` (enrichment DEPTH).
# =============================================================================

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelQuality(StrEnum):
    """The quality the work is done at — resolved to a concrete model by the
    tenant catalogue. Distinct from EnrichmentConfig's `quality_tier`."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModelCapabilities(BaseModel):
    """Capability gating used by quality→model resolution (e.g. a vision role
    SHALL resolve to a vision-capable model)."""

    model_config = ConfigDict(extra="forbid")

    vision: bool = False
    tool_calling: bool = False
    context_window: int = Field(ge=1)


class _RegistryFields(BaseModel):
    """Shared descriptive fields of a registry model. No secret here — the key
    and endpoint URL live on the referenced provider."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=120)
    """Human, MUTABLE name. Unique among models. Used for agent-route config
    and display — NOT a stable reference key (that is `model_id`)."""
    provider_id: str = Field(min_length=1)
    """Stable id of the provider (endpoint + credential) this model is served
    by. References survive a provider rename."""
    upstream_model: str = Field(min_length=1)
    """The model id AT the provider, e.g. `claude-haiku-4-5-20251001` or
    `gpt-4o`. The platform sends `<provider.wire_format>/<upstream_model>` to
    litellm with the provider's base_url + key injected per call."""
    capabilities: ModelCapabilities
    provider_cost_in_per_1m: float = Field(ge=0.0)
    provider_cost_out_per_1m: float = Field(ge=0.0)
    default_model_quality: ModelQuality
    enabled: bool = True


class LLMModelUpsert(_RegistryFields):
    """Write body for a model's metadata. On create the service mints the
    `model_id`; on update the `model_id` is the path key and every field here
    (including `alias`) may change."""


class LLMRegistryEntry(_RegistryFields):
    """The STORED document (Arango `_key = model_id`)."""

    model_id: str = Field(min_length=1)
    effective_from: str
    """ISO-8601 UTC timestamp of the last write (effective-dated)."""

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.model_id
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> LLMRegistryEntry:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)

    def to_public(self) -> LLMRegistryPublic:
        return LLMRegistryPublic(
            model_id=self.model_id,
            alias=self.alias,
            provider_id=self.provider_id,
            upstream_model=self.upstream_model,
            capabilities=self.capabilities,
            provider_cost_in_per_1m=self.provider_cost_in_per_1m,
            provider_cost_out_per_1m=self.provider_cost_out_per_1m,
            default_model_quality=self.default_model_quality,
            enabled=self.enabled,
            effective_from=self.effective_from,
        )


class LLMRegistryPublic(_RegistryFields):
    """API READ projection. Carries no secret (the key is on the provider)."""

    model_id: str
    effective_from: str


class LLMRegistryListResponse(BaseModel):
    """GET response — the full model registry as public projections."""

    model_config = ConfigDict(extra="forbid")

    models: list[LLMRegistryPublic]
