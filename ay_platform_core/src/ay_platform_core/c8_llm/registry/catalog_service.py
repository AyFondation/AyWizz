# =============================================================================
# File: catalog_service.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/catalog_service.py
# Description: Business logic for the per-tenant LLM catalogue + the
#              quality→model RESOLUTION. v2: catalogue rows reference the stable
#              `model_id` (not the mutable alias), so renames/edits never orphan
#              a tenant's curation. Resolution returns the model's CURRENT alias
#              (the platform routes by alias → the C8 injector resolves the
#              provider). NO cross-quality fallback (explicit > surprising).
#              v3 (800 v10): the tenant catalogue is OPT-OUT — on a tenant's
#              first touch it is LAZILY MATERIALISED with every platform-registry
#              model (enabled), then marked initialised so a later "remove all"
#              is not undone. `list_available_models` powers the HMI's re-add
#              picker (registry models NOT currently in the tenant catalogue).
# =============================================================================

from __future__ import annotations

from ay_platform_core.c8_llm.registry.catalog_models import (
    ProjectModelsResponse,
    ResolvedModel,
    TenantCatalogEntry,
    TenantCatalogModelPublic,
    TenantCatalogUpsert,
)
from ay_platform_core.c8_llm.registry.catalog_repository import CatalogStore
from ay_platform_core.c8_llm.registry.models import LLMRegistryPublic, ModelQuality
from ay_platform_core.c8_llm.registry.service import LLMRegistryService


class ModelNotInRegistryError(LookupError):
    """Raised when a catalogue op references a model_id absent from the registry."""


class TenantCatalogService:
    """Application service for the per-tenant LLM catalogue."""

    def __init__(self, repo: CatalogStore, registry: LLMRegistryService) -> None:
        self._repo = repo
        self._registry = registry

    # ---- Opt-out lazy materialisation (800 v10) ---------------------------

    async def _ensure_initialized(self, tenant_id: str) -> None:
        """On a tenant's FIRST touch, materialise its catalogue with every
        platform-registry model (enabled), then mark it initialised. Idempotent
        and cheap thereafter (one marker read). An admin who subsequently removes
        models is NOT re-populated, because the marker persists independently of
        the catalogue rows."""
        if await self._repo.is_initialized(tenant_id):
            return
        for rp in await self._registry.list_models():
            entry = TenantCatalogEntry(
                tenant_id=tenant_id, model_id=rp.model_id, enabled=True
            )
            await self._repo.upsert(entry.to_document())
        await self._repo.mark_initialized(tenant_id)

    async def list_available_models(self, tenant_id: str) -> list[LLMRegistryPublic]:
        """Registry models NOT currently in the tenant's catalogue — the set an
        admin can (re-)add via the HMI picker. Materialises defaults first so a
        brand-new tenant reports an empty 'available' set (all already catalogued)."""
        await self._ensure_initialized(tenant_id)
        catalogued = {
            raw["model_id"] for raw in await self._repo.list_for_tenant(tenant_id)
        }
        return [
            rp
            for rp in await self._registry.list_models()
            if rp.model_id not in catalogued
        ]

    # ---- CRUD -------------------------------------------------------------

    async def upsert_model(
        self, tenant_id: str, model_id: str, body: TenantCatalogUpsert
    ) -> TenantCatalogModelPublic:
        """Add/configure a registry model in a tenant's catalogue. Raises
        ModelNotInRegistryError if the model_id is unknown to the registry."""
        await self._ensure_initialized(tenant_id)
        registry_public = await self._registry.get_model(model_id)
        if registry_public is None:
            raise ModelNotInRegistryError(model_id)
        entry = TenantCatalogEntry(
            tenant_id=tenant_id,
            model_id=model_id,
            enabled=body.enabled,
            rate_in_per_1m=body.rate_in_per_1m,
            rate_out_per_1m=body.rate_out_per_1m,
            markup_pct=body.markup_pct,
            default_for_new_projects=body.default_for_new_projects,
        )
        await self._repo.upsert(entry.to_document())
        return _join(entry, registry_public)

    async def remove_model(self, tenant_id: str, model_id: str) -> bool:
        """Remove a model from a tenant's catalogue. False if it wasn't there."""
        return await self._repo.delete(tenant_id, model_id)

    async def list_catalog(self, tenant_id: str) -> list[TenantCatalogModelPublic]:
        """List a tenant's catalogue joined with the registry public view.
        Rows whose registry model has since been deleted are skipped (stale).
        Materialises the opt-out defaults on the tenant's first touch (800 v10)."""
        await self._ensure_initialized(tenant_id)
        rows = await self._repo.list_for_tenant(tenant_id)
        out: list[TenantCatalogModelPublic] = []
        for raw in rows:
            entry = TenantCatalogEntry.from_document(raw)
            registry_public = await self._registry.get_model(entry.model_id)
            if registry_public is None:
                continue
            out.append(_join(entry, registry_public))
        return out

    # ---- Per-project model associations (lazy defaults) -------------------

    async def get_project_models(
        self, tenant_id: str, project_id: str
    ) -> ProjectModelsResponse:
        """A project's EFFECTIVE model list: the explicit set if configured,
        else the tenant's `default_for_new_projects` set (lazy default)."""
        await self._ensure_initialized(tenant_id)
        catalogue = await self.list_catalog(tenant_id)
        by_id = {c.model_id: c for c in catalogue}
        doc = await self._repo.get_project_models(tenant_id, project_id)
        if doc is not None:
            model_ids = [m for m in doc.get("model_ids", []) if m in by_id]
            is_explicit = True
        else:
            model_ids = [c.model_id for c in catalogue if c.default_for_new_projects]
            is_explicit = False
        return ProjectModelsResponse(
            tenant_id=tenant_id,
            project_id=project_id,
            model_ids=model_ids,
            is_explicit=is_explicit,
            models=[by_id[m] for m in model_ids if m in by_id],
        )

    async def set_project_models(
        self, tenant_id: str, project_id: str, model_ids: list[str]
    ) -> ProjectModelsResponse:
        """Set a project's EXPLICIT model list. Every id MUST be in the tenant
        catalogue (else ModelNotInRegistryError)."""
        catalogue = {c.model_id for c in await self.list_catalog(tenant_id)}
        for mid in model_ids:
            if mid not in catalogue:
                raise ModelNotInRegistryError(mid)
        await self._repo.set_project_models(
            {
                "_key": f"{tenant_id}:{project_id}",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "model_ids": list(dict.fromkeys(model_ids)),  # dedup, keep order
            }
        )
        return await self.get_project_models(tenant_id, project_id)

    async def _effective_model_ids(
        self, tenant_id: str, project_id: str | None
    ) -> set[str] | None:
        """The model_ids the resolution is scoped to, or None for 'whole
        catalogue' (no project given, OR no explicit set and no defaults)."""
        if project_id is None:
            return None
        doc = await self._repo.get_project_models(tenant_id, project_id)
        if doc is not None:
            return set(doc.get("model_ids", []))
        defaults = {
            c.model_id
            for c in await self.list_catalog(tenant_id)
            if c.default_for_new_projects
        }
        return defaults or None

    # ---- Resolution — quality → concrete model ----------------------------

    async def resolve(
        self,
        tenant_id: str,
        model_quality: ModelQuality,
        *,
        project_id: str | None = None,
        require_vision: bool = False,
        require_tool_calling: bool = False,
    ) -> ResolvedModel | None:
        """Resolve a project's `model_quality` to a concrete model for `tenant_id`,
        SCOPED to the project's effective model set (explicit list, else tenant
        defaults, else the whole catalogue). None when no enabled,
        capability-satisfying model of the requested quality qualifies."""
        await self._ensure_initialized(tenant_id)
        scope = await self._effective_model_ids(tenant_id, project_id)
        rows = await self._repo.list_for_tenant(tenant_id)
        candidates: list[LLMRegistryPublic] = []
        for raw in rows:
            entry = TenantCatalogEntry.from_document(raw)
            if not entry.enabled:
                continue
            if scope is not None and entry.model_id not in scope:
                continue
            rp = await self._registry.get_model(entry.model_id)
            if rp is None or not rp.enabled:
                continue
            if rp.default_model_quality != model_quality:
                continue
            if require_vision and not rp.capabilities.vision:
                continue
            if require_tool_calling and not rp.capabilities.tool_calling:
                continue
            candidates.append(rp)
        if not candidates:
            return None
        # Cheapest input cost wins; deterministic tie-break on the alias.
        best = min(candidates, key=lambda r: (r.provider_cost_in_per_1m, r.alias))
        return ResolvedModel(
            model_id=best.model_id,
            model_alias=best.alias,
            model_quality=model_quality,
            upstream_model=best.upstream_model,
            vision=best.capabilities.vision,
            tool_calling=best.capabilities.tool_calling,
        )


def _join(
    entry: TenantCatalogEntry, registry_public: LLMRegistryPublic
) -> TenantCatalogModelPublic:
    return TenantCatalogModelPublic(
        tenant_id=entry.tenant_id,
        model_id=entry.model_id,
        enabled=entry.enabled,
        rate_in_per_1m=entry.rate_in_per_1m,
        rate_out_per_1m=entry.rate_out_per_1m,
        markup_pct=entry.markup_pct,
        default_for_new_projects=entry.default_for_new_projects,
        registry=registry_public,
    )
