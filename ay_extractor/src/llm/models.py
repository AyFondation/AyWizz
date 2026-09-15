# src/llm/models.py — v1
"""LLM-specific types: Message, ImageInput, LLMResponse.

See spec §27.3 for full documentation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Message(BaseModel):
    """Single message in a conversation."""

    role: Literal["user", "assistant", "system"]
    content: str


class ImageInput(BaseModel):
    """Image payload for vision-enabled completions."""

    data: bytes
    media_type: str
    source_id: str | None = None


class LLMResponse(BaseModel):
    """Normalized response from any LLM provider."""

    content: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model: str
    provider: str
    latency_ms: int
    raw_response: Any = None
