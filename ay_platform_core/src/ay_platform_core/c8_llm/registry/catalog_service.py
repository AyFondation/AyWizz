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

# Quality is ORDINAL — that is the whole reason "at least this tier" is a
# meaningful filter. Kept as an explicit map rather than relying on the
# StrEnum's declaration order, which is an implementation detail that a
# reordering or an inserted tier would silently change.
_QUALITY_RANK: dict[ModelQuality, int] = {
    ModelQuality.LOW: 0,
    ModelQuality.MEDIUM: 1,
    ModelQuality.HIGH: 2,
}

# ASSUMED TOKEN MIX. Ranking on input price alone described almost none of
# the bill; ranking on the two prices requires knowing their ratio, and the
# platform cannot know it per call. 4:1 input:output is the shape of this
# platform's traffic — large RAG context, moderate replies — and it is an
# ASSUMPTION, stated here so it can be argued with rather than discovered.
#
# IT IS ALSO REPLACEABLE WITH A MEASUREMENT. C8 already records real
# `prompt_tokens` / `completion_tokens` per call in `llm_calls`; the honest
# successor to this constant is that observed ratio per agent, not a better
# guess. Until then a wrong ratio can only mis-rank models whose in/out
# prices are inverted relative to each other — it cannot pick a model that
# fails a capability or a quality floor.
_ASSUMED_INPUT_SHARE = 0.8
_ASSUMED_OUTPUT_SHARE = 0.2

# What provider-side prompt caching is worth on the input half. Cache reads
# bill at a fraction of fresh input, and this platform re-sends a long,
# stable system prompt on nearly every call — the case caching was built
# for. Deliberately CONSERVATIVE: crediting the full discount would make a
# caching model win on a saving it only realises once the prompt exceeds the
# provider's minimum cacheable length, which short calls never do.
_CACHING_INPUT_DISCOUNT = 0.5


def _effective_cost(model: LLMRegistryPublic) -> float:
    """Blended price per million tokens, discounted for measured caching.

    Used only to RANK candidates that have already cleared the quality floor
    and every capability gate, so it never trades correctness for price — it
    chooses between models that would all have worked.
    """
    input_price = model.provider_cost_in_per_1m
    if model.capabilities.supports("prompt_caching"):
        input_price *= _CACHING_INPUT_DISCOUNT
    return (
        input_price * _ASSUMED_INPUT_SHARE
        + model.provider_cost_out_per_1m * _ASSUMED_OUTPUT_SHARE
    )


def _composite_score(
    model: LLMRegistryPublic, benchmarks: dict[str, float],
) -> float | None:
    """Weighted mean of the model's scores over `benchmarks`, or None.

    `None` when the model is missing ANY benchmark the profile weights. That
    strictness is the point: a mean taken over a different subset for each
    model compares two numbers that do not mean the same thing, and the
    model scored on only its best benchmark would win every time. Partial
    coverage is not a smaller measurement — it is a different one.
    """
    total_weight = sum(w for w in benchmarks.values() if w > 0)
    if total_weight <= 0:
        return None
    weighted = 0.0
    for name, weight in benchmarks.items():
        if weight <= 0:
            continue
        entry = model.quality_scores.get(name)
        if entry is None:
            return None
        weighted += entry.score * weight
    return weighted / total_weight


def _best_value(
    candidates: list[LLMRegistryPublic], benchmarks: dict[str, float],
) -> LLMRegistryPublic:
    """Pick on quality-per-euro over a weighted benchmark PROFILE, else price.

    The ratio is what the operator actually wants: not the cheapest model,
    and not the best one, but the most quality per unit of spend among those
    that already clear the quality floor and every capability gate.

    THE PROFILE COMES FROM THE CALLER, and that is the whole design. There is
    no "the quality" of a model: a coding leaderboard says nothing about
    document synthesis, and most work is judged on SEVERAL benchmarks at
    different weights. Only the work knows its own mix — the agent→profile
    table lives in `key_provider`, where the agent name arrives. Choosing a
    profile here would mean C8 learning about pipeline phases.

    SCORED AND UNSCORED MODELS ARE NEVER COMPARED. A model missing a
    benchmark the profile weights has no position on that axis, and giving
    it a default — 0, or the mean, or the tier's midpoint — would be a
    fabricated measurement deciding real spending. So: if ANY candidate
    covers the whole profile, the choice is made among those; if none does,
    or the profile is empty, it falls back to price alone. Scoring one model
    is therefore immediately meaningful without silently demoting the rest
    to an invented value.

    The tie-break on alias is not cosmetic: without it two identically
    ranked models resolve differently between calls, and a cost report
    becomes impossible to reconcile.
    """
    if not benchmarks:
        return min(candidates, key=lambda r: (_effective_cost(r), r.alias))

    scored = [
        (c, s) for c in candidates
        if (s := _composite_score(c, benchmarks)) is not None
    ]
    if not scored:
        return min(candidates, key=lambda r: (_effective_cost(r), r.alias))

    def _ratio(pair: tuple[LLMRegistryPublic, float]) -> tuple[float, str]:
        model, score = pair
        cost = _effective_cost(model)
        if cost <= 0.0:
            # A free model is unbeatable on ratio; guard the division rather
            # than let it raise or produce an infinity that sorts oddly.
            return (-float("inf"), model.alias)
        # Negated so a HIGHER ratio sorts first under `min`, keeping the
        # alias tie-break in the same ascending comparison.
        return (-(score / cost), model.alias)

    return min(scored, key=_ratio)[0]


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
        require_structured_output: bool = False,
        require_streaming: bool = False,
        benchmarks: dict[str, float] | None = None,
    ) -> ResolvedModel | None:
        """Resolve a project's `model_quality` to a concrete model for `tenant_id`,
        SCOPED to the project's effective model set (explicit list, else tenant
        defaults, else the whole catalogue). None when no enabled,
        capability-satisfying model of AT LEAST the requested quality qualifies.

        SELECTION IS QUALITY-THEN-PRICE, not price alone (2026-09-29). Two
        defects were fixed here at once, and they compounded:

          1. The tier was matched EXACTLY, so asking for `medium` never
             considered a `high` model — even one that costs less. A better
             model at a lower price is strictly preferable on both axes, and
             the platform was declining it. Quality is ordinal, so "at least
             the requested tier" is a well-defined widening, and it is the
             half of quality/price the platform can honestly compute.

          2. The winner was `min(provider_cost_in_per_1m)` — INPUT cost only.
             Output is what a `generate` phase is dominated by, and a model
             that is cheap to prompt and expensive to answer won on a number
             that described almost none of the bill.

        `benchmarks` NAMES WHICH QUALITY, as a weighted profile — a coding
        leaderboard for a `generate` phase, a summarisation one for
        ingestion, or a mix at chosen weights. Resolution then maximises
        composite-score-per-euro. Omit it and the choice is on price alone
        among everything that already qualifies, which is the honest
        behaviour when nothing is known about relative quality.

        The caller supplies it rather than the platform inferring it because
        the platform cannot: only the work knows what it is asking for, and
        a wrong inference here spends real money on the wrong model. The
        agent→profile table lives in `key_provider`, next to the agent→tier
        table it complements.
        """
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
            if _QUALITY_RANK[rp.default_model_quality] < _QUALITY_RANK[model_quality]:
                continue
            # EFFECTIVE capability, not the raw flag (R-800-152): a capability
            # the model has but the operator disabled must not be selected for.
            # Reading `.vision` here would answer "can it" when the question
            # is "may we".
            if require_vision and not rp.capabilities.supports("vision"):
                continue
            if require_tool_calling and not rp.capabilities.supports("tool_calling"):
                continue
            if require_structured_output and not rp.capabilities.supports(
                "structured_output"
            ):
                continue
            if require_streaming and not rp.capabilities.supports("streaming"):
                continue
            candidates.append(rp)
        if not candidates:
            return None
        profile = benchmarks or {}
        best = _best_value(candidates, profile)
        composite = _composite_score(best, profile)
        # Only claim the profile decided when it actually could: it was
        # non-empty AND the winner covers all of it. Recording the request
        # instead of the outcome would make a price-only fallback look like
        # a quality judgement. Sorted so the label is stable across runs.
        decided_by = ",".join(sorted(profile)) if composite is not None else None
        return ResolvedModel(
            model_id=best.model_id,
            model_alias=best.alias,
            # The tier the model ACTUALLY is, which may exceed what was asked
            # when a better model turned out to be cheaper. Reporting the
            # requested tier here would hide the upgrade from the call log
            # and from anyone auditing what the money bought.
            model_quality=best.default_model_quality,
            upstream_model=best.upstream_model,
            # What the caller MAY use, which is what it will act on.
            vision=best.capabilities.supports("vision"),
            tool_calling=best.capabilities.supports("tool_calling"),
            structured_output=best.capabilities.supports("structured_output"),
            streaming=best.capabilities.supports("streaming"),
            prompt_caching=best.capabilities.supports("prompt_caching"),
            decided_by_benchmark=decided_by,
            composite_score=composite,
            effective_cost=_effective_cost(best),
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
