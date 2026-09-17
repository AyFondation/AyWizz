# =============================================================================
# File: models.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/models.py
# Description: Pydantic contracts for the platform LLM model registry.
#
#              v3 (R-800-152): capabilities gain `thinking` and, more
#              importantly, PROVENANCE. These flags are not descriptive — they
#              GATE routing: `catalog_service` refuses a model whose
#              `tool_calling` is false when the resolution requires it. So a
#              box ticked wrongly in the HMI does not fail at configuration
#              time, it fails inside an agent run, where "the agent never edits
#              a file" is several layers from "someone guessed a capability".
#              `CapabilityProvenance` records, per flag, whether the value was
#              MEASURED against the model, DECLARED by the provider's metadata,
#              ASSERTED by a human, or never established at all.
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


class CapabilityEvidence(StrEnum):
    """How a capability flag came to hold its value (R-800-152).

    The distinction that matters operationally is MEASURED vs everything
    else: only `measured` means the platform exercised the capability and
    watched what happened. `unknown` is deliberately separate from a `False`
    value — "we never checked" and "we checked and it cannot" lead an
    operator to different actions."""

    MEASURED = "measured"
    DECLARED = "declared"
    ASSERTED = "asserted"
    UNKNOWN = "unknown"


class CapabilityProvenance(BaseModel):
    """Per-flag provenance for `ModelCapabilities`.

    Spelled out field by field rather than a `dict[str, …]` so mypy --strict
    catches a capability added to one model and forgotten in the other. The
    cost is one extra edit per new capability; the benefit is that the
    omission is a build error instead of a silently `unknown` flag."""

    model_config = ConfigDict(extra="forbid")

    vision: CapabilityEvidence = CapabilityEvidence.UNKNOWN
    tool_calling: CapabilityEvidence = CapabilityEvidence.UNKNOWN
    thinking: CapabilityEvidence = CapabilityEvidence.UNKNOWN
    context_window: CapabilityEvidence = CapabilityEvidence.UNKNOWN


class CapabilityOverrides(BaseModel):
    """The operator's SUBTRACTIVE override (R-800-152).

    `True` means "the model can do this, and we choose not to use it" — for
    cost, policy or determinism. The override can only ever take away: there
    is deliberately no way to express "enable a capability the model lacks",
    because that is not a preference, it is a false claim, and it is the exact
    failure this whole mechanism exists to remove."""

    model_config = ConfigDict(extra="forbid")

    vision: bool = False
    tool_calling: bool = False
    thinking: bool = False


class ModelCapabilities(BaseModel):
    """What a model CAN do, how we know, and what we choose to use.

    Three distinct things that used to be one checkbox:
      - the flag itself — what the model can do, established by MEASUREMENT ;
      - `provenance` — how that value came to be, so a measurement is never
        confused with a guess ;
      - `disabled` — the operator's choice not to use a capability that
        exists.

    Read routing decisions through `supports()`, never off the raw flag.
    Defaults keep pre-v3 documents readable: they come back with everything
    `unknown` and nothing disabled, which is exactly their state."""

    model_config = ConfigDict(extra="forbid")

    vision: bool = False
    tool_calling: bool = False
    thinking: bool = False
    """Extended / adaptive reasoning. Gated like the others because a route
    that asks for thinking on a model without it is rejected upstream, not
    silently downgraded."""
    context_window: int = Field(ge=1)
    """EXEMPT from measurement, per R-800-152: `context_window` can only be
    established empirically with a maximum-length request, whose cost is not
    proportionate to its value. It stays `declared` or `asserted`."""
    provenance: CapabilityProvenance = Field(default_factory=CapabilityProvenance)
    disabled: CapabilityOverrides = Field(default_factory=CapabilityOverrides)

    def supports(self, capability: str) -> bool:
        """Effective capability: `measured AND NOT disabled-by-operator`.

        The single place routing should ask. Reading `caps.vision` directly
        answers "can it" when the question is almost always "may we"."""
        return bool(getattr(self, capability, False)) and not bool(
            getattr(self.disabled, capability, False)
        )


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
