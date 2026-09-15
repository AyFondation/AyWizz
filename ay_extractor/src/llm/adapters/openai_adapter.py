# src/llm/adapters/openai_adapter.py — v2
"""OpenAI-compatible adapter implementing BaseLLMClient.

D-020 v2 §B1 routing: this adapter is now the SOLE LLM adapter shipped in
AyExtractor (anthropic/google/ollama/openrouter were stripped in session 2).
In-cluster, the `base_url` constructor kwarg (or `OPENAI_BASE_URL` env)
points it at C8 LiteLLM (`http://c8:8000/v1`) — provider routing is
handled C8-side via `agent_routes`. Standalone dev: set `base_url` to any
OpenAI-API-compatible endpoint (Anthropic's `/v1/openai`, Ollama's `/v1`,
LiteLLM standalone, …) without touching this code.

Uses the official openai SDK. Supports vision and structured outputs.
See spec §27.4; D-020 R-100-125 v2 for the C8-routing invariant.
"""

from __future__ import annotations

import time
from typing import Any

from ayextractor.llm.base_client import BaseLLMClient
from ayextractor.llm.models import ImageInput, LLMResponse, Message
from pydantic import BaseModel


def _system_message(system: str, cache: bool) -> dict[str, Any]:
    """Build the system message. When `cache` is set, the system prompt is sent
    as a structured text block carrying `cache_control: ephemeral` — Anthropic
    prompt caching, forwarded verbatim by the LiteLLM proxy. Below the model's
    minimum cacheable size the API silently skips caching (safe no-op), so this
    is always safe to request ; it only ever helps when the same large prefix
    recurs across calls in the cache window."""
    if cache:
        return {
            "role": "system",
            "content": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
        }
    return {"role": "system", "content": system}


class OpenAIAdapter(BaseLLMClient):
    """OpenAI-compatible LLM adapter (routed through C8 in-cluster)."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ):
        """Initialise the adapter.

        Args:
            model: Model id (D-020: the C8 `agent_routes` *route alias* such
                as ``claude-sonnet-midtier`` — C8 resolves it to the upstream
                model).
            api_key: Bearer key. In-cluster: `C8_GATEWAY_API_KEY`. Standalone:
                whatever the upstream endpoint expects.
            base_url: OpenAI-API endpoint. Default (None) falls back to the
                openai SDK's resolution of `OPENAI_BASE_URL` env (or the
                official `https://api.openai.com/v1`). D-020 in-cluster
                deployment SHALL set this to `http://c8:8000/v1`.
        """
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        # Attribution headers forwarded to C8/LiteLLM → recorded by the cost
        # forwarder on each `llm_calls` row (X-Run-Id / X-Source-Id / …), so the
        # per-source ingestion cost is aggregatable (R-400-226 / R-800-070).
        self._headers = headers or None

    def _client(self) -> Any:
        """Lazy openai SDK client — keeps the import inside the method so
        the package is OPTIONAL at install time (extras = `llm`).
        """
        import openai

        return openai.AsyncOpenAI(
            api_key=self._api_key or None,
            base_url=self._base_url,
            default_headers=self._headers,
        )

    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        response_format: type[BaseModel] | None = None,
        cache_system: bool = False,
    ) -> LLMResponse:
        client = self._client()
        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append(_system_message(system, cache_system))
        for m in messages:
            oai_messages.append({"role": m.role, "content": m.content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format is not None:
            schema = response_format.model_json_schema()
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": response_format.__name__, "schema": schema},
            }

        t0 = time.monotonic()
        resp = await client.chat.completions.create(**kwargs)
        latency = int((time.monotonic() - t0) * 1000)

        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=self._model,
            provider="openai",
            latency_ms=latency,
            raw_response=resp,
        )

    async def complete_with_vision(
        self,
        messages: list[Message],
        images: list[ImageInput],
        system: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = False,
    ) -> LLMResponse:
        import base64

        client = self._client()
        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append(_system_message(system, cache_system))

        # Build multimodal content
        content_parts: list[dict[str, Any]] = []
        for m in messages:
            content_parts.append({"type": "text", "text": m.content})
        for img in images:
            b64 = base64.b64encode(img.data).decode()
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.media_type};base64,{b64}"},
            })
        oai_messages.append({"role": "user", "content": content_parts})

        t0 = time.monotonic()
        resp = await client.chat.completions.create(
            model=self._model, messages=oai_messages, max_tokens=max_tokens,
        )
        latency = int((time.monotonic() - t0) * 1000)

        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=self._model,
            provider="openai",
            latency_ms=latency,
            raw_response=resp,
        )

    @property
    def supports_vision(self) -> bool:
        return True

    @property
    def provider_name(self) -> str:
        # D-020 v2: this adapter speaks the OpenAI API surface; the upstream
        # provider (Claude via C8 LiteLLM, OpenAI direct, Ollama, …) depends
        # on the configured `base_url`. The literal "openai" identifier
        # refers to the wire protocol, not the provider.
        return "openai"
