# =============================================================================
# File: test_registry_service.py
# Path: ay_platform_core/tests/unit/c8_llm/test_registry_service.py
# Description: Unit tests for LLMRegistryService (v2): create mints a stable
#              model_id, update by id changes every attribute (incl. alias)
#              while preserving the id, alias uniqueness is enforced (409), and
#              the service holds NO secret (key lives on the provider).
# =============================================================================

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi import HTTPException

from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    ModelCapabilities,
    ModelQuality,
)
from ay_platform_core.c8_llm.registry.service import (
    LLMRegistryService,
    ModelNotFoundError,
)

pytestmark = pytest.mark.asyncio


class _FakeRepo:
    """In-memory RegistryStore keyed by model_id, with an alias index."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.store[document["_key"]] = dict(document)

    async def get(self, model_id: str) -> dict[str, Any] | None:
        return self.store.get(model_id)

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        for doc in self.store.values():
            if doc.get("alias") == alias:
                return doc
        return None

    async def list_all(self) -> list[dict[str, Any]]:
        return [dict(v) for _, v in sorted(self.store.items())]

    async def delete(self, model_id: str) -> bool:
        return self.store.pop(model_id, None) is not None


def _service() -> tuple[LLMRegistryService, _FakeRepo]:
    repo = _FakeRepo()
    ids = (f"id{i}" for i in itertools.count())
    svc = LLMRegistryService(
        repo, clock=lambda: "2026-06-08T00:00:00+00:00", id_factory=lambda: next(ids)
    )
    return svc, repo


def _body(alias: str = "haiku", **over: Any) -> LLMModelUpsert:
    base: dict[str, Any] = {
        "alias": alias,
        "provider_id": "p1",
        "upstream_model": "claude-haiku-4-5",
        "capabilities": ModelCapabilities(
            vision=True, tool_calling=True, context_window=200000
        ),
        "provider_cost_in_per_1m": 0.8,
        "provider_cost_out_per_1m": 4.0,
        "default_model_quality": ModelQuality.LOW,
        "enabled": True,
    }
    base.update(over)
    return LLMModelUpsert.model_validate(base)


async def test_create_mints_id_and_keys_on_it() -> None:
    svc, repo = _service()
    public = await svc.create_model(_body("haiku"))
    assert public.model_id == "id0"
    assert public.alias == "haiku"
    assert "id0" in repo.store


async def test_duplicate_alias_create_is_409() -> None:
    svc, _ = _service()
    await svc.create_model(_body("dup"))
    with pytest.raises(HTTPException) as exc:
        await svc.create_model(_body("dup"))
    assert exc.value.status_code == 409


async def test_update_preserves_id_and_changes_alias() -> None:
    svc, _ = _service()
    created = await svc.create_model(_body("old"))
    updated = await svc.update_model(
        created.model_id, _body("new", provider_cost_in_per_1m=1.5)
    )
    assert updated.model_id == created.model_id  # stable id
    assert updated.alias == "new"
    assert updated.provider_cost_in_per_1m == 1.5
    # The old alias is gone; the new one resolves to the same id.
    assert await svc.get_model_by_alias("old") is None
    by_new = await svc.get_model_by_alias("new")
    assert by_new is not None and by_new.model_id == created.model_id


async def test_update_unknown_id_raises() -> None:
    svc, _ = _service()
    with pytest.raises(ModelNotFoundError):
        await svc.update_model("ghost", _body())


async def test_update_to_a_taken_alias_is_409() -> None:
    svc, _ = _service()
    await svc.create_model(_body("taken"))
    other = await svc.create_model(_body("free"))
    with pytest.raises(HTTPException) as exc:
        await svc.update_model(other.model_id, _body("taken"))
    assert exc.value.status_code == 409


async def test_delete_model() -> None:
    svc, repo = _service()
    created = await svc.create_model(_body("m"))
    assert await svc.delete_model(created.model_id) is True
    assert created.model_id not in repo.store
    assert await svc.delete_model(created.model_id) is False
