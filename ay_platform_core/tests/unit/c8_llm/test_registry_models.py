# =============================================================================
# File: test_registry_models.py
# Path: ay_platform_core/tests/unit/c8_llm/test_registry_models.py
# Description: Unit tests for the provider + model registry pydantic contracts
#              (v2): stable ids, mutable alias, provider reference, write-only
#              provider key projection, document round-trips, and `base_url`
#              normalisation (trailing-slash strip + http(s) scheme check —
#              provider_models v2, the production outage of 2026-09-09).
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


# ---- base_url normalisation (2026-09-09) -------------------------------------
#
# A trailing slash used to survive into storage, and litellm appends its own
# path: `https://api.anthropic.com/` + `/v1/messages` = a double slash, HTTP 404
# with an EMPTY body, surfaced to the end user as `AnthropicException - .`.
# Total outage, trivially caused, undiagnosable without the proxy pod's logs.


def _upsert(base_url: str) -> LLMProviderUpsert:
    return LLMProviderUpsert.model_validate(
        {"name": "X", "base_url": base_url, "wire_format": "anthropic"}
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The exact production defect.
        ("https://api.anthropic.com/", "https://api.anthropic.com"),
        # Several slashes, and whitespace from a paste.
        ("https://api.anthropic.com///", "https://api.anthropic.com"),
        ("  https://api.anthropic.com/  ", "https://api.anthropic.com"),
        # A path-bearing base URL keeps its path, loses only the trailing slash.
        ("https://api.openai.com/v1/", "https://api.openai.com/v1"),
        # Plain http is legitimate for an in-cluster self-hosted endpoint.
        ("http://ollama:11434/", "http://ollama:11434"),
        # Already clean → untouched.
        ("https://api.anthropic.com", "https://api.anthropic.com"),
    ],
)
def test_base_url_trailing_slash_is_stripped(raw: str, expected: str) -> None:
    assert _upsert(raw).base_url == expected


@pytest.mark.parametrize(
    "raw",
    [
        "api.anthropic.com",       # scheme missing — litellm cannot route it
        "ftp://api.anthropic.com",  # wrong scheme
        "/v1/messages",             # a path, not an endpoint
        "https://",                 # degenerates to empty once stripped
        "   ",                      # whitespace only
    ],
)
def test_base_url_without_a_usable_http_scheme_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        _upsert(raw)


def test_normalisation_applies_to_the_stored_entry_not_just_the_upsert() -> None:
    """Every writer builds on `_ProviderFields`, so the stored document is
    normalised too — the HMI upsert, the seed and the entry share one rule."""
    entry = LLMProviderEntry(
        provider_id="p1",
        name="Anthropic",
        base_url="https://api.anthropic.com/",
        wire_format="anthropic",
        effective_from="2026-09-09T00:00:00+00:00",
    )
    assert entry.base_url == "https://api.anthropic.com"
    assert entry.to_document()["base_url"] == "https://api.anthropic.com"
    assert entry.to_public().base_url == "https://api.anthropic.com"
