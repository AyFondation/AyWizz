# src/llm/base_client.py — v1
"""Abstract LLM client interface.

See spec §27.2 for full documentation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ayextractor.llm.models import ImageInput, LLMResponse, Message
from pydantic import BaseModel


class BaseLLMClient(ABC):
    """Unified interface for all LLM providers."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        response_format: type[BaseModel] | None = None,
        cache_system: bool = False,
    ) -> LLMResponse:
        """Text completion.

        `cache_system` opts the (stable) system prompt into Anthropic prompt
        caching (LiteLLM passthrough). It is a SAFE no-op below the model's
        minimum cacheable size (Haiku 4096 / Sonnet 1024 tokens) — the API
        silently skips caching. Set it when the same large system prefix is
        reused across many calls in a short window."""

    @abstractmethod
    async def complete_with_vision(
        self,
        messages: list[Message],
        images: list[ImageInput],
        system: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> LLMResponse:
        """Vision-enabled completion (images + text). See `complete` for
        `cache_system`."""

    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether this provider/model supports image inputs."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (anthropic, openai, google, ollama)."""
