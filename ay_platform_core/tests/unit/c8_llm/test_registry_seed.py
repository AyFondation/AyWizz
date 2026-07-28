# =============================================================================
# File: test_registry_seed.py
# Path: ay_platform_core/tests/unit/c8_llm/test_registry_seed.py
# Description: Unit tests for the provider + model registry seeding (v2). The
#              canonical litellm-config maps to ONE provider per wire_format
#              (explicit base_url) + models referencing it by id, no key seeded,
#              and `seed_missing` is idempotent (existing aliases + the
#              provider's operator-set key survive a re-seed).
# =============================================================================

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
import yaml

from ay_platform_core.c8_llm.config import (
    LiteLLMConfig,
    LiteLLMParams,
    ModelEntry,
    ModelInfo,
)
from ay_platform_core.c8_llm.registry.models import ModelQuality
from ay_platform_core.c8_llm.registry.seed import (
    _quality_for,
    _split_upstream,
    seed_missing,
)

pytestmark = pytest.mark.asyncio

_CANONICAL_CONFIG = (
    Path(__file__).resolve().parents[4]
    / "infra"
    / "c8_gateway"
    / "config"
    / "litellm-config.yaml"
)


def _canonical() -> LiteLLMConfig:
    raw = yaml.safe_load(_CANONICAL_CONFIG.read_text(encoding="utf-8"))
    return LiteLLMConfig.model_validate(raw)


class _FakeRegistry:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.store[document["_key"]] = dict(document)

    async def get(self, model_id: str) -> dict[str, Any] | None:
        return self.store.get(model_id)

    async def get_by_alias(self, alias: str) -> dict[str, Any] | None:
        for d in self.store.values():
            if d.get("alias") == alias:
                return d
        return None

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self.store.values())

    async def delete(self, model_id: str) -> bool:
        return self.store.pop(model_id, None) is not None


class _FakeProviders:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}

    async def upsert(self, document: dict[str, Any]) -> None:
        self.store[document["_key"]] = dict(document)

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return self.store.get(provider_id)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        for d in self.store.values():
            if d.get("name") == name:
                return d
        return None

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self.store.values())

    async def delete(self, provider_id: str) -> bool:
        return self.store.pop(provider_id, None) is not None


def _ids() -> Any:
    return (f"x{i}" for i in itertools.count()).__next__


async def test_seed_creates_provider_and_models() -> None:
    reg, prov = _FakeRegistry(), _FakeProviders()
    created = await seed_missing(
        reg, prov, _canonical(),
        clock=lambda: "2026-06-08T00:00:00+00:00", id_factory=_ids(),
    )
    assert set(created) == {"claude-haiku-fast", "claude-sonnet-midtier", "claude-opus-flagship"}
    # One Anthropic provider with an EXPLICIT base_url.
    providers = list(prov.store.values())
    assert len(providers) == 1
    assert providers[0]["name"] == "Anthropic"
    assert providers[0]["base_url"] == "https://api.anthropic.com"
    assert providers[0]["wire_format"] == "anthropic"
    # Models reference it by id; upstream is the bare model (no provider prefix).
    haiku = await reg.get_by_alias("claude-haiku-fast")
    assert haiku is not None
    assert haiku["provider_id"] == providers[0]["_key"]
    assert haiku["upstream_model"] == "claude-haiku-4-5-20251001"
    assert haiku["default_model_quality"] == ModelQuality.LOW.value


async def test_seed_skips_the_wildcard_entry() -> None:
    reg, prov = _FakeRegistry(), _FakeProviders()
    await seed_missing(reg, prov, _canonical(), id_factory=_ids())
    # The litellm `*` pass-through must NOT become a model or a provider.
    assert await reg.get_by_alias("*") is None
    assert all(p["name"] != "*" for p in prov.store.values())


async def test_seed_is_idempotent_and_preserves_provider_key() -> None:
    reg, prov = _FakeRegistry(), _FakeProviders()
    await seed_missing(reg, prov, _canonical(), id_factory=_ids())
    # Operator sets a key on the provider.
    pid = next(iter(prov.store))
    prov.store[pid]["api_key_ciphertext"] = "ay.1.k1.X.Y"

    second = await seed_missing(reg, prov, _canonical(), id_factory=_ids())
    assert second == []  # nothing re-created
    assert prov.store[pid]["api_key_ciphertext"] == "ay.1.k1.X.Y"  # key survived
    assert len(prov.store) == 1  # provider reused, not duplicated


def _entry(litellm_model: str) -> ModelEntry:
    return ModelEntry(
        model_name="custom",
        litellm_params=LiteLLMParams(model=litellm_model),
        model_info=ModelInfo(
            display_name="Custom",
            features=[],
            context_window=1000,
            cost_per_million_input=1.0,
            cost_per_million_output=2.0,
        ),
    )


@pytest.mark.parametrize(
    ("litellm_model", "expected"),
    [
        ("anthropic/claude-opus-4-8", ModelQuality.HIGH),
        ("anthropic/claude-sonnet-5", ModelQuality.MEDIUM),
        ("anthropic/claude-haiku-4-5", ModelQuality.LOW),
        # No opus/sonnet/haiku token → the MEDIUM default (covers the fallthrough).
        ("openai/gpt-4o", ModelQuality.MEDIUM),
        ("some-local-model", ModelQuality.MEDIUM),
    ],
)
async def test_quality_for_maps_tier_or_defaults_medium(
    litellm_model: str, expected: ModelQuality
) -> None:
    assert _quality_for(_entry(litellm_model)) is expected


async def test_split_upstream_with_and_without_provider_prefix() -> None:
    assert _split_upstream("anthropic/claude-opus-4-8") == ("anthropic", "claude-opus-4-8")
    # No "/" → the whole string is BOTH the wire format and the upstream model.
    assert _split_upstream("some-local-model") == ("some-local-model", "some-local-model")
