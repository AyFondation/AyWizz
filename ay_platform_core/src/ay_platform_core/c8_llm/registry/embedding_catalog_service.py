# =============================================================================
# File: embedding_catalog_service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/embedding_catalog_service.py
# Description: Per-tenant EMBEDDING catalogue business logic (D-011). The tenant
#              exposes a subset of the platform embedding registry, marks one
#              DEFAULT, and each project SELECTS one model (embeddings are index-
#              consistent — one per project). DELETE-GUARDS protect projects:
#              a catalogue entry cannot be removed while a project selects it,
#              and the platform-level model delete is refused while it is in any
#              tenant catalogue or project selection (`assert_model_deletable`).
#
# @relation implements:R-400-226
# @relation implements:R-400-229
# =============================================================================

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from ay_platform_core.c8_llm.registry.embedding_catalog_models import (
    EmbeddingCatalogEntry,
    EmbeddingCatalogModelPublic,
    EmbeddingCatalogUpsert,
    ProjectEmbeddingResponse,
    ProjectEmbeddingSelection,
)
from ay_platform_core.c8_llm.registry.embedding_catalog_repository import (
    EmbeddingCatalogStore,
    EmbeddingProjectStore,
)
from ay_platform_core.c8_llm.registry.embedding_models import (
    EmbeddingModelEntry,
    EmbeddingModelPublic,
)
from ay_platform_core.c8_llm.registry.embedding_repository import EmbeddingModelStore


class ModelInUseError(Exception):
    """Raised when a model delete/removal is blocked by a project selection."""

    def __init__(self, project_ids: list[str]) -> None:
        self.project_ids = project_ids
        super().__init__(f"model in use by projects: {project_ids}")


class EmbeddingCatalogService:
    def __init__(
        self,
        catalog_repo: EmbeddingCatalogStore,
        project_repo: EmbeddingProjectStore,
        model_repo: EmbeddingModelStore,
        reembed_notifier: Any | None = None,
    ) -> None:
        self._catalog = catalog_repo
        self._project = project_repo
        self._models = model_repo
        # Optional ReembedNotifier: switching a project to a DIFFERENT model
        # triggers a best-effort re-embed of that project (R-400-229).
        self._reembed = reembed_notifier

    async def _model_public(self, model_id: str) -> EmbeddingModelPublic | None:
        doc = await self._models.get(model_id)
        return None if doc is None else EmbeddingModelEntry.from_document(doc).to_public()

    # ---- Tenant catalogue -------------------------------------------------

    async def list_catalog(self, tenant_id: str) -> list[EmbeddingCatalogModelPublic]:
        out: list[EmbeddingCatalogModelPublic] = []
        for raw in await self._catalog.list_for_tenant(tenant_id):
            entry = EmbeddingCatalogEntry.from_document(raw)
            model = await self._model_public(entry.model_id)
            if model is None:  # model deleted from the registry — skip the stale row
                continue
            out.append(
                EmbeddingCatalogModelPublic(
                    tenant_id=entry.tenant_id,
                    model_id=entry.model_id,
                    enabled=entry.enabled,
                    default_for_new_projects=entry.default_for_new_projects,
                    registry=model,
                )
            )
        return out

    async def list_available(self, tenant_id: str) -> list[EmbeddingModelPublic]:
        """Platform-registry embedding models NOT yet in this tenant's catalogue
        — the tenant admin's add picker (the platform registry list itself is
        platform_manager-only). Enabled models only."""
        catalogued = {
            EmbeddingCatalogEntry.from_document(r).model_id
            for r in await self._catalog.list_for_tenant(tenant_id)
        }
        out: list[EmbeddingModelPublic] = []
        for raw in await self._models.list_all():
            entry = EmbeddingModelEntry.from_document(raw)
            if entry.enabled and entry.model_id not in catalogued:
                out.append(entry.to_public())
        return out

    async def upsert_catalog(
        self, tenant_id: str, model_id: str, body: EmbeddingCatalogUpsert
    ) -> EmbeddingCatalogModelPublic:
        """Add/configure a model in the tenant catalogue. 404 when the model is
        not in the platform registry. Setting `default_for_new_projects` clears
        the previous tenant default (exactly one)."""
        model = await self._model_public(model_id)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown embedding model id: {model_id}",
            )
        if body.default_for_new_projects:
            await self._clear_tenant_default(tenant_id, keep=model_id)
        entry = EmbeddingCatalogEntry(
            tenant_id=tenant_id,
            model_id=model_id,
            enabled=body.enabled,
            default_for_new_projects=body.default_for_new_projects,
        )
        await self._catalog.upsert(entry.to_document())
        return EmbeddingCatalogModelPublic(
            tenant_id=tenant_id,
            model_id=model_id,
            enabled=entry.enabled,
            default_for_new_projects=entry.default_for_new_projects,
            registry=model,
        )

    async def _clear_tenant_default(self, tenant_id: str, *, keep: str) -> None:
        for raw in await self._catalog.list_for_tenant(tenant_id):
            entry = EmbeddingCatalogEntry.from_document(raw)
            if entry.default_for_new_projects and entry.model_id != keep:
                await self._catalog.upsert(
                    entry.model_copy(update={"default_for_new_projects": False}).to_document()
                )

    async def remove_from_catalog(self, tenant_id: str, model_id: str) -> bool:
        """Remove a model from the tenant catalogue. 409 while a project in the
        tenant still selects it."""
        users = [
            s
            for s in await self._project.list_using_model(model_id)
            if s.get("tenant_id") == tenant_id
        ]
        if users:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"embedding model {model_id} is used by "
                    f"{len(users)} project(s); reassign them first"
                ),
            )
        return await self._catalog.delete(
            EmbeddingCatalogEntry.make_key(tenant_id, model_id)
        )

    # ---- Per-project selection -------------------------------------------

    async def set_project_embedding(
        self, tenant_id: str, project_id: str, model_id: str
    ) -> ProjectEmbeddingResponse:
        """Select the project's embedding. 422 when the model is not ENABLED in
        the tenant catalogue (a project can only pick what the tenant exposes)."""
        cat = await self._catalog.get(EmbeddingCatalogEntry.make_key(tenant_id, model_id))
        if cat is None or not EmbeddingCatalogEntry.from_document(cat).enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"model {model_id} is not enabled in the tenant catalogue",
            )
        prev = await self._project.get(
            ProjectEmbeddingSelection.make_key(tenant_id, project_id)
        )
        prev_model = (
            ProjectEmbeddingSelection.from_document(prev).model_id
            if prev is not None
            else None
        )
        await self._project.upsert(
            ProjectEmbeddingSelection(
                tenant_id=tenant_id, project_id=project_id, model_id=model_id
            ).to_document()
        )
        # Switching to a DIFFERENT model changes the project's vectors → trigger
        # a best-effort re-embed. First-time selection has no prior vectors.
        if self._reembed is not None and prev_model is not None and prev_model != model_id:
            await self._reembed.notify(
                tenant_id=tenant_id, project_id=project_id, model_id=model_id
            )
        return await self.get_project_embedding(tenant_id, project_id)

    async def get_project_embedding(
        self, tenant_id: str, project_id: str
    ) -> ProjectEmbeddingResponse:
        """The project's effective embedding: explicit selection, else the tenant
        default (lazy), else None."""
        sel = await self._project.get(
            ProjectEmbeddingSelection.make_key(tenant_id, project_id)
        )
        if sel is not None:
            mid = ProjectEmbeddingSelection.from_document(sel).model_id
            return ProjectEmbeddingResponse(
                tenant_id=tenant_id, project_id=project_id, model_id=mid,
                is_explicit=True, model=await self._model_public(mid),
            )
        default_id = await self._tenant_default(tenant_id)
        return ProjectEmbeddingResponse(
            tenant_id=tenant_id, project_id=project_id, model_id=default_id,
            is_explicit=False,
            model=None if default_id is None else await self._model_public(default_id),
        )

    async def _tenant_default(self, tenant_id: str) -> str | None:
        for raw in await self._catalog.list_for_tenant(tenant_id):
            entry = EmbeddingCatalogEntry.from_document(raw)
            if entry.default_for_new_projects and entry.enabled:
                return entry.model_id
        return None

    # ---- Platform-level delete guard -------------------------------------

    async def assert_model_deletable(self, model_id: str) -> None:
        """Raise ModelInUseError when a platform model delete would strand a
        project's selection. Checked by the registry model DELETE endpoint."""
        projects = [
            str(s.get("project_id"))
            for s in await self._project.list_using_model(model_id)
        ]
        if projects:
            raise ModelInUseError(projects)
