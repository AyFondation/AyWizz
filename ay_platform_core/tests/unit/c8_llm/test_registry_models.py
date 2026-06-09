# =============================================================================
# File: test_registry_models.py
# Path: ay_platform_core/tests/unit/c8_llm/test_registry_models.py
# Description: Unit tests for the provider + model registry pydantic contracts
#              (v2): stable ids, mutable alias, provider reference, write-only
#              provider key projection, document round-trips.
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    LLMRegistryEntry,
    ModelCapabilities,
    ModelQuality,
)
from ay_platform_core.c8_llm.registry.provider_models import (
    LLMProviderEntry,
    LLMProviderUpsert,
)


def _caps() -> ModelCapabilities:
    return ModelCapabilities(vision=True, tool_calling=True, context_window=200000)


def _entry(model_id: str = "m1", alias: str = "haiku") -> LLMRegistryEntry:
    return LLMRegistryEntry(
        model_id=model_id,
        alias=alias,
        provider_id="p1",
        upstream_model="claude-haiku-4-5",
        capabilities=_caps(),
        provider_cost_in_per_1m=0.8,
        provider_cost_out_per_1m=4.0,
        default_model_quality=ModelQuality.LOW,
        effective_from="2026-06-08T00:00:00+00:00",
    )


def test_entry_document_round_trip_keys_on_model_id() -> None:
    doc = _entry().to_document()
    assert doc["_key"] == "m1"
    assert doc["alias"] == "haiku"
    assert doc["provider_id"] == "p1"
    back = LLMRegistryEntry.from_document(doc)
    assert back == _entry()


def test_public_projection_carries_no_secret() -> None:
    public = _entry().to_public()
    dumped = public.model_dump()
    assert dumped["model_id"] == "m1"
    assert dumped["alias"] == "haiku"
    # No key field of any kind on the model projection (key is on the provider).
    assert "api_key" not in dumped
    assert "api_key_ciphertext" not in dumped
    assert "api_base" not in dumped


def test_upsert_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        LLMModelUpsert.model_validate(
            {
                "alias": "x",
                "provider_id": "p1",
                "upstream_model": "m",
                "capabilities": _caps().model_dump(),
                "provider_cost_in_per_1m": 0.0,
                "provider_cost_out_per_1m": 0.0,
                "default_model_quality": "low",
                "api_key": "leak",  # not a field → rejected
            }
        )


# ---- Provider models ---------------------------------------------------------


def _provider() -> LLMProviderEntry:
    return LLMProviderEntry(
        provider_id="p1",
        name="Anthropic",
        base_url="https://api.anthropic.com",
        wire_format="anthropic",
        effective_from="2026-06-08T00:00:00+00:00",
    )


def test_provider_round_trip_and_write_only_key() -> None:
    doc = _provider().to_document()
    assert doc["_key"] == "p1"
    public = _provider().to_public()
    dumped = public.model_dump()
    assert dumped["base_url"] == "https://api.anthropic.com"
    assert dumped["key_status"] == "not_set"
    assert "api_key" not in dumped and "api_key_ciphertext" not in dumped


def test_provider_key_status_reflects_ciphertext() -> None:
    keyed = _provider().model_copy(
        update={"api_key_ciphertext": "ay.1.x", "api_key_hint": "…abcd"}
    )
    assert keyed.to_public().key_status == "set"


def test_provider_base_url_is_mandatory() -> None:
    with pytest.raises(ValidationError):
        LLMProviderUpsert.model_validate({"name": "X", "wire_format": "openai"})
