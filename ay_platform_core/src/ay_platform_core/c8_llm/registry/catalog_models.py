# =============================================================================
# File: catalog_models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/catalog_models.py
# Description: Pydantic contracts for the per-tenant LLM catalogue (MIDDLE layer
#              of the 3-layer model). v2: a catalogue row references a model by
#              its STABLE `model_id` (was the mutable alias) — keyed
#              `{tenant_id}:{model_id}` — so renaming a model or changing any
#              attribute never orphans a tenant's catalogue. Carries NO secret;
#              MAY carry an optional chargeback rate-card (explicit per-1M rates
#              OR a markup_pct over the platform cost, never both).
# =============================================================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ay_platform_core.c8_llm.registry.models import (
    LLMRegistryPublic,
    ModelQuality,
)


class TenantCatalogUpsert(BaseModel):
    """PUT body to add / configure a model in a tenant's catalogue. The
    `model_id` is the URL path key and MUST exist in the platform registry
    (enforced by the service). The rate-card is OPTIONAL and mutually-exclusive:
    explicit per-1M rates OR a markup percentage, never both."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    rate_in_per_1m: float | None = Field(default=None, ge=0.0)
    rate_out_per_1m: float | None = Field(default=None, ge=0.0)
    markup_pct: float | None = Field(default=None, ge=0.0)
    default_for_new_projects: bool = False
    """When True, this model is auto-associated to a tenant project that has no
    explicit model list yet (lazy default — see ProjectModels)."""

    @model_validator(mode="after")
    def _exclusive_rate_card(self) -> TenantCatalogUpsert:
        has_explicit = self.rate_in_per_1m is not None or self.rate_out_per_1m is not None
        if has_explicit and self.markup_pct is not None:
            raise ValueError(
                "rate-card is either explicit rate_in/out_per_1m OR markup_pct, "
                "not both"
            )
        return self


class TenantCatalogEntry(BaseModel):
    """The STORED document (Arango `_key = {tenant_id}:{model_id}`)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    enabled: bool = True
    rate_in_per_1m: float | None = Field(default=None, ge=0.0)
    rate_out_per_1m: float | None = Field(default=None, ge=0.0)
    markup_pct: float | None = Field(default=None, ge=0.0)
    default_for_new_projects: bool = False

    @staticmethod
    def make_key(tenant_id: str, model_id: str) -> str:
        return f"{tenant_id}:{model_id}"

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.make_key(self.tenant_id, self.model_id)
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> TenantCatalogEntry:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)


class TenantCatalogModelPublic(BaseModel):
    """API READ projection of one catalogue entry, joined with the registry's
    public view (which carries the model's CURRENT alias + attributes)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    model_id: str
    enabled: bool
    rate_in_per_1m: float | None = None
    rate_out_per_1m: float | None = None
    markup_pct: float | None = None
    default_for_new_projects: bool = False
    registry: LLMRegistryPublic


class TenantCatalogListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[TenantCatalogModelPublic]


class ProjectModelsUpdate(BaseModel):
    """PUT body — set the explicit model list a project may use (a subset of the
    tenant catalogue, by model_id)."""

    model_config = ConfigDict(extra="forbid")

    model_ids: list[str]


class ProjectModelsResponse(BaseModel):
    """A project's EFFECTIVE model list + whether it was set explicitly. When
    `is_explicit` is False the list is the tenant's `default_for_new_projects`
    set (lazy default)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str
    model_ids: list[str]
    is_explicit: bool
    models: list[TenantCatalogModelPublic]
    """The associated catalogue entries (joined), for the HMI."""


class ResolvedModel(BaseModel):
    """Result of resolving `(tenant, model_quality)` → a concrete model. The
    stable `model_id` plus the CURRENT `model_alias` the platform routes by
    (the C8 injector turns that alias into the provider call). Used by the
    project picker (HMI) and by ingestion (EnrichmentConfig)."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    model_alias: str
    model_quality: ModelQuality
    upstream_model: str
    vision: bool
    tool_calling: bool
