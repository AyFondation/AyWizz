# =============================================================================
# File: provider_models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/registry/provider_models.py
# Description: Pydantic contracts for the LLM PROVIDER registry (the endpoint +
#              credential layer). A provider is referenced by registry models
#              via a STABLE technical id (`provider_id`); its `name` and
#              `base_url` are mutable without breaking those references. The API
#              key lives HERE (one credential per endpoint/account), encrypted
#              with SecretCipher and write-only by construction — same three-
#              layer discipline as the model registry. `base_url` is MANDATORY:
#              the platform is provider-agnostic and never relies on a built-in
#              default. `wire_format` is the litellm provider family (openai /
#              anthropic / azure …) that fixes the request shape + auth header.
# =============================================================================

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ProviderFields(BaseModel):
    """Shared, non-secret descriptive fields of a provider."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    """Human, mutable display name. Unique among providers. e.g. "Anthropic",
    "OpenAI prod", "vLLM internal"."""
    base_url: str = Field(min_length=1)
    """MANDATORY upstream endpoint URL. e.g. https://api.anthropic.com,
    https://api.openai.com/v1, http://vllm.internal/v1. Injected per call; never
    defaulted."""
    wire_format: str = Field(min_length=1)
    """LiteLLM provider family that fixes the request format + auth header.
    e.g. anthropic, openai, azure, mistral, gemini. For an OpenAI-compatible
    self-hosted server use `openai`."""


class LLMProviderUpsert(_ProviderFields):
    """PUT body for provider metadata (no key)."""


class LLMProviderEntry(_ProviderFields):
    """The STORED document (Arango `_key = provider_id`). `api_key_ciphertext`
    is a SecretCipher token (AAD `llm_provider:<provider_id>:api_key`) or None.
    NEVER serialised into an HTTP response — use `to_public()`."""

    provider_id: str = Field(min_length=1)
    api_key_ciphertext: str | None = None
    api_key_hint: str = ""
    effective_from: str

    def to_document(self) -> dict[str, Any]:
        doc = self.model_dump()
        doc["_key"] = self.provider_id
        return doc

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> LLMProviderEntry:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return cls.model_validate(clean)

    def to_public(self) -> LLMProviderPublic:
        return LLMProviderPublic(
            provider_id=self.provider_id,
            name=self.name,
            base_url=self.base_url,
            wire_format=self.wire_format,
            effective_from=self.effective_from,
            key_status="set" if self.api_key_ciphertext else "not_set",
            api_key_hint=self.api_key_hint,
        )


class LLMProviderPublic(_ProviderFields):
    """API READ projection — no ciphertext, only a non-reversible key status."""

    provider_id: str
    effective_from: str
    key_status: Literal["set", "not_set"]
    api_key_hint: str = ""


class LLMProviderApiKeyUpdate(BaseModel):
    """PUT body of the dedicated write-only provider-key endpoint."""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, repr=False)


class LLMProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[LLMProviderPublic]
