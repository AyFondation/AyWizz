# =============================================================================
# File: embedding_catalog_models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_catalog_models.py
# Description: Pydantic contracts for the per-tenant EMBEDDING catalogue (D-011,
#              middle layer). The root operator registers embedding models
#              (embedding_models.py); the tenant makes a subset AVAILABLE and
#              picks a DEFAULT, and each project SELECTS one (embeddings are
#              index-consistent — one model per project, not a set like chat).
#              Carries no secret; the key stays on the provider.
# =============================================================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c8_llm.registry.embedding_models import EmbeddingModelPublic


class EmbeddingCatalogUpsert(BaseModel):
    """PUT body to add / configure an embedding model in a tenant's catalogue.
    The `model_id` is the URL path key and MUST exist in the platform registry."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_for_new_projects: bool = False
    """When True, this model is the tenant default a project inherits when it has
    made no explicit selection (exactly one default per tenant — the service
    unsets the previous one)."""


class EmbeddingCatalogEntry(BaseModel):
    """The STORED document (Arango `_key = {tenant_id}:{model_id}`)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    enabled: bool = True
    default_for_new_projects: bool = False

    @staticmethod
    def make_key(tenant_id: str, model_id: str) -> str:
        return f"{tenant_id}:{model_id}"

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.make_key(self.tenant_id, self.model_id)
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> EmbeddingCatalogEntry:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)


class EmbeddingCatalogModelPublic(BaseModel):
    """API READ projection of one catalogue entry, joined with the registry's
    current public view of the model (alias + dimension)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    model_id: str
    enabled: bool
    default_for_new_projects: bool
    registry: EmbeddingModelPublic


class EmbeddingCatalogListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[EmbeddingCatalogModelPublic]


class ProjectEmbeddingUpdate(BaseModel):
    """PUT body — the single embedding model a project uses (by model_id). It
    MUST be enabled in the tenant catalogue."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1)


class ProjectEmbeddingResponse(BaseModel):
    """A project's EFFECTIVE embedding model. `is_explicit` False → the value is
    the tenant default (lazy). `model` is None when nothing is configured yet
    (ingestion/retrieval then have no embedder → surfaced as a clear error)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str
    model_id: str | None
    is_explicit: bool
    model: EmbeddingModelPublic | None


class ProjectEmbeddingSelection(BaseModel):
    """The STORED per-project selection (Arango `_key = {tenant_id}:{project_id}`)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)

    @staticmethod
    def make_key(tenant_id: str, project_id: str) -> str:
        return f"{tenant_id}:{project_id}"

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.make_key(self.tenant_id, self.project_id)
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> ProjectEmbeddingSelection:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)
