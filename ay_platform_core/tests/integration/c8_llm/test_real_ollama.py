# =============================================================================
# File: test_real_ollama.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_llm/test_real_ollama.py
# Description: Integration tests against a REAL LLM (Ollama running a small
#              Qwen2.5-0.5B-instruct model in a testcontainer). Complements
#              `test_client_end_to_end.py` which uses a mock — here we
#              verify the client speaks correctly to a real OpenAI-
#              compatible endpoint (Ollama's /v1).
#
#              The platform is multi-LLM by design; exercising at least
#              one concrete adapter against its server catches protocol-
#              shape drift that mocks always pass.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)
from tests.fixtures.containers import OllamaEndpoint

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_ollama_chat_completion_round_trip(
    ollama_container: OllamaEndpoint,
) -> None:
    """POST a minimal prompt; assert the response parses into
    ChatCompletionResponse with non-empty content and a sensible usage
    block. Ollama's /v1 endpoint must match the OpenAI subset the client
    expects."""
    settings = ClientSettings(gateway_url=ollama_container.api_v1_url)
    client = LLMGatewayClient(settings, bearer_token="ignored-by-ollama")
    try:
        payload = ChatCompletionRequest(
            model=ollama_container.model_id,
            messages=[
                ChatMessage(
                    role=ChatRole.SYSTEM,
                    content="Answer in a single short sentence.",
                ),
                ChatMessage(role=ChatRole.USER, content="Say hello."),
            ],
            max_tokens=32,
            temperature=0.1,
        )
        resp = await client.chat_completion(
            payload,
            agent_name="integration-test",
            session_id="sess-ollama-smoke",
        )
    finally:
        await client.aclose()

    assert resp.choices, "Ollama returned no choices"
    choice = resp.choices[0]
    assert choice.message.role == ChatRole.ASSISTANT
    assert isinstance(choice.message.content, str)
    assert choice.message.content.strip(), "Ollama returned empty content"
    # Ollama includes a token usage block in OpenAI-compatible mode.
    assert resp.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_ollama_respects_max_tokens(
    ollama_container: OllamaEndpoint,
) -> None:
    """Passing `max_tokens=4` SHALL cap the completion size — proves the
    client forwards the parameter correctly and Ollama honours it."""
    settings = ClientSettings(gateway_url=ollama_container.api_v1_url)
    client = LLMGatewayClient(settings, bearer_token="x")
    try:
        payload = ChatCompletionRequest(
            model=ollama_container.model_id,
            messages=[
                ChatMessage(
                    role=ChatRole.USER,
                    content="Write a very long story about a dragon.",
                )
            ],
            max_tokens=4,
            temperature=0.1,
        )
        resp = await client.chat_completion(
            payload,
            agent_name="integration-test",
            session_id="sess-ollama-maxtokens",
        )
    finally:
        await client.aclose()

    # The model is free to use fewer tokens, but NOT more.
    assert resp.usage.completion_tokens <= 4, (
        f"Ollama ignored max_tokens=4, returned {resp.usage.completion_tokens}"
    )
