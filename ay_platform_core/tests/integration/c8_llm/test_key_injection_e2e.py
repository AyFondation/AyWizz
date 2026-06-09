# =============================================================================
# File: test_key_injection_e2e.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_llm/test_key_injection_e2e.py
# Description: END-TO-END integration of the provider-normalised call path
#              against a REAL ArangoDB + a mock LiteLLM proxy. Exercises the
#              whole chain: provider key encrypted at rest (AES-256-GCM) →
#              RegistryKeyProvider resolves alias → model → provider, decrypts
#              the key, REWRITES the request model to `<wire_format>/<upstream>`
#              and injects the provider's api_base + key → the proxy receives
#              the per-call upstream override. The real repos + cipher + client
#              wired together (not a mock of the unit under test).
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI, Request

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import ChatCompletionRequest, ChatMessage, ChatRole
from ay_platform_core.c8_llm.registry.key_provider import build_registry_key_provider
from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    ModelCapabilities,
    ModelQuality,
)
from ay_platform_core.c8_llm.registry.provider_models import LLMProviderUpsert
from ay_platform_core.c8_llm.registry.provider_repository import LLMProviderRepository
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


def _build_proxy(captured: dict[str, Any]) -> FastAPI:
    """Mock LiteLLM proxy: captures the per-request overrides the client sends."""
    app = FastAPI()

    @app.post("/v1/chat/completions", response_model=None)
    async def completions(request: Request) -> dict[str, Any]:
        body = await request.json()
        captured["api_key"] = body.get("api_key")
        captured["api_base"] = body.get("api_base")
        captured["model"] = body.get("model")
        return {
            "id": "mock-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": body.get("model") or "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    return app


@pytest_asyncio.fixture(scope="function")
async def wired(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[tuple[Any, LLMRegistryService, LLMProviderService]]:
    db_name = f"c8_keyinj_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    repo = LLMRegistryRepository(db)
    repo._ensure_collections_sync()
    provider_repo = LLMProviderRepository(db)
    provider_repo._ensure_collections_sync()
    # The SAME deterministic keyring used by build_registry_key_provider.
    cipher = SecretCipher.from_env()
    try:
        yield db, LLMRegistryService(repo), LLMProviderService(provider_repo, cipher)
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(db: Any, proxy_app: FastAPI) -> LLMGatewayClient:
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app), base_url="http://proxy/v1"
    )
    return LLMGatewayClient(
        ClientSettings(gateway_url="http://proxy/v1"),
        bearer_token="test-token",
        http_client=http,
        key_provider=build_registry_key_provider(db),
    )


async def _seed_model(
    registry: LLMRegistryService, provider: LLMProviderService, *, with_key: str | None
) -> None:
    prov = await provider.create_provider(
        LLMProviderUpsert(
            name="Anthropic", base_url="https://api.anthropic.com", wire_format="anthropic"
        )
    )
    if with_key is not None:
        await provider.set_api_key(prov.provider_id, with_key)
    await registry.create_model(
        LLMModelUpsert(
            alias="claude-haiku-fast",
            provider_id=prov.provider_id,
            upstream_model="claude-haiku-4-5-20251001",
            capabilities=ModelCapabilities(
                vision=True, tool_calling=True, context_window=200000
            ),
            provider_cost_in_per_1m=0.8,
            provider_cost_out_per_1m=4.0,
            default_model_quality=ModelQuality.LOW,
        )
    )


def _payload(model: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model, messages=[ChatMessage(role=ChatRole.USER, content="hi")]
    )


async def test_provider_key_decrypted_model_rewritten_end_to_end(
    wired: tuple[Any, LLMRegistryService, LLMProviderService],
) -> None:
    db, registry, provider = wired
    plaintext = f"sk-ant-{uuid.uuid4().hex}"
    await _seed_model(registry, provider, with_key=plaintext)

    captured: dict[str, Any] = {}
    client = _client(db, _build_proxy(captured))
    resp = await client.chat_completion(
        _payload("claude-haiku-fast"), agent_name="c3-rag", session_id="s1"
    )
    await client.aclose()

    assert resp.choices[0].message.content == "ok"
    # The proxy received the REWRITTEN model + provider base + decrypted key.
    assert captured["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert captured["api_base"] == "https://api.anthropic.com"
    assert captured["api_key"] == plaintext


async def test_model_without_provider_key_still_rewrites(
    wired: tuple[Any, LLMRegistryService, LLMProviderService],
) -> None:
    db, registry, provider = wired
    await _seed_model(registry, provider, with_key=None)

    captured: dict[str, Any] = {}
    client = _client(db, _build_proxy(captured))
    await client.chat_completion(
        _payload("claude-haiku-fast"), agent_name="c3-rag", session_id="s1"
    )
    await client.aclose()
    # Model rewritten + api_base injected; no key (proxy env fallback).
    assert captured["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert captured["api_base"] == "https://api.anthropic.com"
    assert captured["api_key"] is None


async def test_unknown_alias_passes_through_unmodified(
    wired: tuple[Any, LLMRegistryService, LLMProviderService],
) -> None:
    db, _registry, _provider = wired
    captured: dict[str, Any] = {}
    client = _client(db, _build_proxy(captured))
    await client.chat_completion(
        _payload("not-in-registry"), agent_name="c3-rag", session_id="s1"
    )
    await client.aclose()
    # No registry entry → no rewrite (the proxy/mock handles the original model).
    assert captured["model"] == "not-in-registry"
    assert captured["api_base"] is None
