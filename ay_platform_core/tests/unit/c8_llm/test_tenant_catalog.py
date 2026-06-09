# =============================================================================
# File: test_tenant_catalog.py
# Path: ay_platform_core/tests/unit/c8_llm/test_tenant_catalog.py
# Description: Unit tests for the per-tenant LLM catalogue (v2: keyed by stable
#              model_id): rate-card exclusivity, the registry-intersection guard,
#              and the quality→model RESOLUTION (quality match, enabled gating at
#              both layers, capability filter, cheapest-wins, no silent
#              downgrade). In-memory fakes + a real LLMRegistryService.
# =============================================================================

from __future__ import annotations

import itertools
from typing import Any

import pytest
from pydantic import ValidationError

from ay_platform_core.c8_llm.registry.catalog_models import (
    TenantCatalogEntry,
    TenantCatalogUpsert,
)
from ay_platform_core.c8_llm.registry.catalog_service import (
    ModelNotInRegistryError,
    TenantCatalogService,
)
from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    ModelCapabilities,
    ModelQuality,
)
from ay_platform_core.c8_llm.registry.service import LLMRegistryService

pytestmark = pytest.mark.asyncio

_TENANT = "tenant-x"


class _FakeStore:
    """In-memory dict keyed by the document `_key`; serves both the registry
    (get/get_by_alias) and the catalogue (variadic key + list_for_tenant)."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.store[document["_key"]] = dict(document)

    async def get(self, *key_parts: str) -> dict[str, Any] | None:
        key = ":".join(key_parts)
        doc = self.store.get(key)
        return dict(doc) if doc is not None else None

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        for d in self.store.values():
            if d.get("alias") == alias:
                return dict(d)
        return None

    async def delete(self, *key_parts: str) -> bool:
        return self.store.pop(":".join(key_parts), None) is not None

    async def list_all(self) -> list[dict[str, Any]]:
        return [dict(v) for v in self.store.values()]

    async def list_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        return [
            dict(v)
            for v in self.store.values()
            if v.get("tenant_id") == tenant_id and "model_ids" not in v
        ]

    async def get_project_models(
        self, tenant_id: str, project_id: str
    ) -> dict[str, Any] | None:
        doc = self.store.get(f"pm:{tenant_id}:{project_id}")
        return dict(doc) if doc is not None else None

    async def set_project_models(self, document: dict[str, Any]) -> None:
        self.store[f"pm:{document['_key']}"] = dict(document)


def _registry() -> LLMRegistryService:
    ids = (f"m{i}" for i in itertools.count())
    return LLMRegistryService(
        _FakeStore(), clock=lambda: "2026-06-08T00:00:00+00:00", id_factory=lambda: next(ids)
    )


async def _seed_registry(
    reg: LLMRegistryService,
    alias: str,
    *,
    quality: ModelQuality,
    cost_in: float,
    vision: bool = False,
    tool_calling: bool = True,
    enabled: bool = True,
) -> str:
    """Create a registry model and return its stable model_id."""
    public = await reg.create_model(
        LLMModelUpsert(
            alias=alias,
            provider_id="p1",
            upstream_model=alias,
            capabilities=ModelCapabilities(
                vision=vision, tool_calling=tool_calling, context_window=200000
            ),
            provider_cost_in_per_1m=cost_in,
            provider_cost_out_per_1m=cost_in * 5,
            default_model_quality=quality,
            enabled=enabled,
        )
    )
    return public.model_id


def _service(reg: LLMRegistryService) -> tuple[TenantCatalogService, _FakeStore]:
    cat_store = _FakeStore()
    return TenantCatalogService(cat_store, reg), cat_store


# ---- Models ------------------------------------------------------------------


async def test_rate_card_explicit_and_markup_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        TenantCatalogUpsert(rate_in_per_1m=1.0, markup_pct=10.0)
    assert TenantCatalogUpsert(rate_in_per_1m=1.0).markup_pct is None
    assert TenantCatalogUpsert(markup_pct=10.0).rate_in_per_1m is None


async def test_entry_key_composition() -> None:
    assert TenantCatalogEntry.make_key("t1", "m1") == "t1:m1"


# ---- CRUD --------------------------------------------------------------------


async def test_upsert_rejects_model_id_absent_from_registry() -> None:
    svc, _ = _service(_registry())
    with pytest.raises(ModelNotInRegistryError):
        await svc.upsert_model(_TENANT, "ghost-id", TenantCatalogUpsert())


async def test_upsert_joins_registry_public_without_key() -> None:
    reg = _registry()
    mid = await _seed_registry(reg, "haiku", quality=ModelQuality.LOW, cost_in=0.8)
    svc, store = _service(reg)
    public = await svc.upsert_model(_TENANT, mid, TenantCatalogUpsert(markup_pct=20.0))
    assert public.model_id == mid
    assert public.registry.alias == "haiku"
    assert public.markup_pct == 20.0
    stored = store.store[f"{_TENANT}:{mid}"]
    assert "api_key" not in str(stored)


async def test_remove_model() -> None:
    reg = _registry()
    mid = await _seed_registry(reg, "haiku", quality=ModelQuality.LOW, cost_in=0.8)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, mid, TenantCatalogUpsert())
    assert await svc.remove_model(_TENANT, mid) is True
    assert await svc.remove_model(_TENANT, mid) is False


async def test_list_skips_rows_whose_registry_model_was_deleted() -> None:
    reg = _registry()
    mid = await _seed_registry(reg, "haiku", quality=ModelQuality.LOW, cost_in=0.8)
    svc, cat_store = _service(reg)
    await svc.upsert_model(_TENANT, mid, TenantCatalogUpsert())
    stale = TenantCatalogEntry(tenant_id=_TENANT, model_id="gone")
    await cat_store.upsert(stale.to_document())
    listed = await svc.list_catalog(_TENANT)
    assert [m.model_id for m in listed] == [mid]


# ---- Resolution --------------------------------------------------------------


async def test_resolve_picks_quality_matched_model() -> None:
    reg = _registry()
    h = await _seed_registry(reg, "haiku", quality=ModelQuality.LOW, cost_in=0.8)
    s = await _seed_registry(reg, "sonnet", quality=ModelQuality.MEDIUM, cost_in=3.0)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, h, TenantCatalogUpsert())
    await svc.upsert_model(_TENANT, s, TenantCatalogUpsert())
    resolved = await svc.resolve(_TENANT, ModelQuality.MEDIUM)
    assert resolved is not None and resolved.model_alias == "sonnet"
    assert resolved.model_id == s


async def test_resolve_cheapest_wins() -> None:
    reg = _registry()
    p = await _seed_registry(reg, "low-pricey", quality=ModelQuality.LOW, cost_in=2.0)
    c = await _seed_registry(reg, "low-cheap", quality=ModelQuality.LOW, cost_in=0.5)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, p, TenantCatalogUpsert())
    await svc.upsert_model(_TENANT, c, TenantCatalogUpsert())
    resolved = await svc.resolve(_TENANT, ModelQuality.LOW)
    assert resolved is not None and resolved.model_alias == "low-cheap"


async def test_resolve_returns_none_without_silent_downgrade() -> None:
    reg = _registry()
    h = await _seed_registry(reg, "haiku", quality=ModelQuality.LOW, cost_in=0.8)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, h, TenantCatalogUpsert())
    assert await svc.resolve(_TENANT, ModelQuality.HIGH) is None


async def test_resolve_skips_tenant_disabled_and_registry_disabled() -> None:
    reg = _registry()
    on = await _seed_registry(reg, "low-on", quality=ModelQuality.LOW, cost_in=0.8)
    off = await _seed_registry(
        reg, "low-reg-off", quality=ModelQuality.LOW, cost_in=0.1, enabled=False
    )
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, off, TenantCatalogUpsert())
    await svc.upsert_model(_TENANT, on, TenantCatalogUpsert(enabled=False))
    assert await svc.resolve(_TENANT, ModelQuality.LOW) is None
    await svc.upsert_model(_TENANT, on, TenantCatalogUpsert(enabled=True))
    resolved = await svc.resolve(_TENANT, ModelQuality.LOW)
    assert resolved is not None and resolved.model_alias == "low-on"


async def test_resolve_capability_filter_vision() -> None:
    reg = _registry()
    nov = await _seed_registry(
        reg, "low-novis", quality=ModelQuality.LOW, cost_in=0.5, vision=False
    )
    vis = await _seed_registry(
        reg, "low-vis", quality=ModelQuality.LOW, cost_in=0.9, vision=True
    )
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, nov, TenantCatalogUpsert())
    await svc.upsert_model(_TENANT, vis, TenantCatalogUpsert())
    plain = await svc.resolve(_TENANT, ModelQuality.LOW)
    assert plain is not None and plain.model_alias == "low-novis"
    v = await svc.resolve(_TENANT, ModelQuality.LOW, require_vision=True)
    assert v is not None and v.model_alias == "low-vis"


async def test_resolve_isolated_per_tenant() -> None:
    reg = _registry()
    h = await _seed_registry(reg, "haiku", quality=ModelQuality.LOW, cost_in=0.8)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, h, TenantCatalogUpsert())
    assert await svc.resolve("tenant-other", ModelQuality.LOW) is None


# ---- Per-project model associations + scoped resolution ----------------------

_PROJECT = "proj-1"


async def test_project_models_default_set_when_not_explicit() -> None:
    reg = _registry()
    a = await _seed_registry(reg, "a", quality=ModelQuality.LOW, cost_in=0.5)
    b = await _seed_registry(reg, "b", quality=ModelQuality.LOW, cost_in=0.9)
    svc, _ = _service(reg)
    # 'a' flagged default; 'b' not.
    await svc.upsert_model(_TENANT, a, TenantCatalogUpsert(default_for_new_projects=True))
    await svc.upsert_model(_TENANT, b, TenantCatalogUpsert())
    res = await svc.get_project_models(_TENANT, _PROJECT)
    assert res.is_explicit is False
    assert res.model_ids == [a]
    assert [m.model_id for m in res.models] == [a]


async def test_set_project_models_overrides_defaults_and_validates() -> None:
    reg = _registry()
    a = await _seed_registry(reg, "a", quality=ModelQuality.LOW, cost_in=0.5)
    b = await _seed_registry(reg, "b", quality=ModelQuality.LOW, cost_in=0.9)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, a, TenantCatalogUpsert(default_for_new_projects=True))
    await svc.upsert_model(_TENANT, b, TenantCatalogUpsert())
    # Explicit set = [b] overrides the default [a].
    res = await svc.set_project_models(_TENANT, _PROJECT, [b])
    assert res.is_explicit is True and res.model_ids == [b]
    # A model not in the tenant catalogue is rejected.
    with pytest.raises(ModelNotInRegistryError):
        await svc.set_project_models(_TENANT, _PROJECT, ["not-catalogued"])


async def test_resolution_is_scoped_to_the_project_set() -> None:
    reg = _registry()
    cheap = await _seed_registry(reg, "cheap", quality=ModelQuality.LOW, cost_in=0.2)
    pricey = await _seed_registry(reg, "pricey", quality=ModelQuality.LOW, cost_in=2.0)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, cheap, TenantCatalogUpsert())
    await svc.upsert_model(_TENANT, pricey, TenantCatalogUpsert())
    # Whole-catalogue resolution → cheapest (cheap).
    whole = await svc.resolve(_TENANT, ModelQuality.LOW)
    assert whole is not None and whole.model_alias == "cheap"
    # Project scoped to ONLY 'pricey' → resolves to pricey (cheap is out of scope).
    await svc.set_project_models(_TENANT, _PROJECT, [pricey])
    scoped = await svc.resolve(_TENANT, ModelQuality.LOW, project_id=_PROJECT)
    assert scoped is not None and scoped.model_alias == "pricey"


async def test_resolution_scoped_to_empty_explicit_set_yields_none() -> None:
    reg = _registry()
    a = await _seed_registry(reg, "a", quality=ModelQuality.LOW, cost_in=0.5)
    svc, _ = _service(reg)
    await svc.upsert_model(_TENANT, a, TenantCatalogUpsert())
    await svc.set_project_models(_TENANT, _PROJECT, [])  # explicitly no models
    assert await svc.resolve(_TENANT, ModelQuality.LOW, project_id=_PROJECT) is None
