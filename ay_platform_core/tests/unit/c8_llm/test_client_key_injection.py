# =============================================================================
# File: test_client_key_injection.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_client_key_injection.py
# Description: Unit tests for the per-request upstream-key injection on
#              LLMGatewayClient (LLM-governance #3). A configured key_provider
#              injects the resolved model's key as the request body `api_key`;
#              the injection is best-effort (no provider / None / provider
#              error → no `api_key`, proxy uses its env fallback). The request
#              body is captured via an httpx MockTransport so the assertion is
#              on the EXACT bytes the proxy would receive.
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import ChatCompletionRequest, ChatMessage, ChatRole
from ay_platform_core.c8_llm.quota.guard import QuotaExceededError
from ay_platform_core.c8_llm.quota.models import (
    QuotaLevelStatus,
    QuotaStatus,
    QuotaWindowStatus,
)
from ay_platform_core.c8_llm.registry.key_provider import CallTarget

pytestmark = pytest.mark.asyncio


def _blocked_status() -> QuotaStatus:
    return QuotaStatus(
        tenant_id="t1",
        windows=[
            QuotaWindowStatus(
                key="session",
                label="Session (5h)",
                duration_seconds=18000,
                anchor="first_use",
                state="exceeded",
                levels=[
                    QuotaLevelStatus(
                        level="tenant",
                        usage_cost_usd=12.0,
                        usage_tokens=0,
                        max_cost_usd=10.0,
                        max_tokens=None,
                        cost_pct=120.0,
                        tokens_pct=None,
                        state="exceeded",
                    )
                ],
            )
        ],
        warned=False,
        blocked=True,
    )


def _ok_response(request: httpx.Request, captured: dict[str, Any]) -> httpx.Response:
    captured["body"] = json.loads(request.content)
    captured["headers"] = dict(request.headers)
    return httpx.Response(
        200,
        json={
            "id": "mock-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "claude-haiku-fast",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def _client(captured: dict[str, Any], **kwargs: Any) -> LLMGatewayClient:
    transport = httpx.MockTransport(lambda req: _ok_response(req, captured))
    http = httpx.AsyncClient(transport=transport, base_url="http://c8:8000/v1")
    return LLMGatewayClient(
        ClientSettings(gateway_url="http://c8:8000/v1"),
        bearer_token="test-token",
        http_client=http,
        **kwargs,
    )


def _payload() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="claude-haiku-fast",
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
    )


async def test_call_target_rewrites_model_and_injects_base_and_key() -> None:
    captured: dict[str, Any] = {}
    seen: list[str] = []

    async def provider(alias: str) -> CallTarget | None:
        seen.append(alias)
        return CallTarget(
            model="anthropic/claude-haiku-4-5",
            api_base="https://api.anthropic.com",
            api_key="sk-ant-registry-key",
        )

    client = _client(captured, key_provider=provider)
    await client.chat_completion(_payload(), agent_name="c3-rag", session_id="s1")
    await client.aclose()

    # The resolver was consulted with the RESOLVED alias.
    assert seen == ["claude-haiku-fast"]
    # The body the proxy receives has the REWRITTEN model + provider base + key.
    assert captured["body"]["model"] == "anthropic/claude-haiku-4-5"
    assert captured["body"]["api_base"] == "https://api.anthropic.com"
    assert captured["body"]["api_key"] == "sk-ant-registry-key"


async def test_model_rewritten_and_base_without_a_key() -> None:
    # A self-hosted provider with no stored key → model rewritten + api_base set,
    # api_key omitted (proxy env fallback). Proves it works key-less.
    captured: dict[str, Any] = {}

    async def provider(alias: str) -> CallTarget | None:
        return CallTarget(
            model="openai/local-model",
            api_base="https://llm.internal/v1",
            api_key=None,
        )

    client = _client(captured, key_provider=provider)
    await client.chat_completion(_payload(), agent_name="c3-rag", session_id="s1")
    await client.aclose()
    assert captured["body"]["model"] == "openai/local-model"
    assert captured["body"]["api_base"] == "https://llm.internal/v1"
    assert "api_key" not in captured["body"]


async def test_no_provider_means_body_unchanged() -> None:
    captured: dict[str, Any] = {}
    client = _client(captured)  # no key_provider
    await client.chat_completion(_payload(), agent_name="c3-rag", session_id="s1")
    await client.aclose()
    assert "api_key" not in captured["body"]
    assert "api_base" not in captured["body"]


async def test_resolver_returning_none_leaves_body_unmodified() -> None:
    captured: dict[str, Any] = {}

    async def provider(alias: str) -> CallTarget | None:
        return None  # unknown alias (e.g. mock model) → no rewrite

    client = _client(captured, key_provider=provider)
    await client.chat_completion(_payload(), agent_name="c3-rag", session_id="s1")
    await client.aclose()
    # Original model preserved (the mock/proxy handles it).
    assert captured["body"]["model"] == "claude-haiku-fast"
    assert "api_key" not in captured["body"]
    assert "api_base" not in captured["body"]


async def test_no_resolved_model_skips_provider() -> None:
    captured: dict[str, Any] = {}
    seen: list[str] = []

    async def provider(alias: str) -> CallTarget | None:
        seen.append(alias)
        return CallTarget(model="anthropic/x", api_base="https://api.anthropic.com", api_key=None)

    client = _client(captured, key_provider=provider)
    payload = ChatCompletionRequest(
        model="", messages=[ChatMessage(role=ChatRole.USER, content="hi")]
    )
    await client.chat_completion(payload, agent_name="unknown-agent", session_id="s1")
    await client.aclose()
    assert seen == []  # guard short-circuited before any resolver call
    assert "api_key" not in captured["body"]


async def test_provider_error_is_swallowed_call_still_succeeds() -> None:
    captured: dict[str, Any] = {}

    async def provider(alias: str) -> CallTarget | None:
        raise RuntimeError("registry unreachable")

    client = _client(captured, key_provider=provider)
    # The call MUST still succeed (proxy env-key fallback), not propagate.
    resp = await client.chat_completion(
        _payload(), agent_name="c3-rag", session_id="s1"
    )
    await client.aclose()
    assert resp.choices[0].message.content == "ok"
    assert "api_key" not in captured["body"]


# --- Prompt caching is provider-aware AND applied after resolution -------------


def _payload_with_system() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="claude-haiku-fast",
        messages=[
            ChatMessage(role=ChatRole.SYSTEM, content="Stable instructions"),
            ChatMessage(role=ChatRole.USER, content="hi"),
        ],
    )


async def test_cache_hint_marks_system_after_anthropic_resolution() -> None:
    # The marker is applied AFTER the model is rewritten to `anthropic/...`,
    # proving the reorder (resolve → then provider-aware cache).
    captured: dict[str, Any] = {}

    async def provider(alias: str) -> CallTarget | None:
        return CallTarget(
            model="anthropic/claude-haiku-4-5",
            api_base="https://api.anthropic.com",
            api_key="sk-ant",
        )

    client = _client(captured, key_provider=provider)
    await client.chat_completion(
        _payload_with_system(), agent_name="c3-rag", session_id="s1", cache_hint="static"
    )
    await client.aclose()
    system = captured["body"]["messages"][0]
    assert system["content"][0]["cache_control"] == {"type": "ephemeral"}


async def test_cache_hint_no_marker_for_openai_provider() -> None:
    # Bug guard: an Anthropic cache_control block sent to OpenAI is rejected, so
    # an openai-resolved call MUST carry no marker even with cache_hint="static".
    captured: dict[str, Any] = {}

    async def provider(alias: str) -> CallTarget | None:
        return CallTarget(
            model="openai/gpt-4o", api_base="https://api.openai.com/v1", api_key="sk-oai"
        )

    client = _client(captured, key_provider=provider)
    await client.chat_completion(
        _payload_with_system(), agent_name="c3-rag", session_id="s1", cache_hint="static"
    )
    await client.aclose()
    # System content stays a plain string — untouched.
    assert captured["body"]["messages"][0]["content"] == "Stable instructions"


# --- Quota enforcement (Lot 3) -------------------------------------------------


async def test_quota_guard_blocks_call_before_send() -> None:
    captured: dict[str, Any] = {}

    async def guard(tenant_id: str, **_kw: Any) -> None:
        raise QuotaExceededError(_blocked_status())

    client = _client(captured, quota_guard=guard)
    with pytest.raises(QuotaExceededError):
        await client.chat_completion(
            _payload(), agent_name="c3-rag", session_id="s1", tenant_id="t1"
        )
    await client.aclose()
    # The hard stop happens BEFORE any upstream POST — nothing was sent.
    assert "body" not in captured


async def test_quota_guard_allows_call_when_ok() -> None:
    captured: dict[str, Any] = {}

    async def guard(tenant_id: str, **_kw: Any) -> None:
        return None

    client = _client(captured, quota_guard=guard)
    resp = await client.chat_completion(
        _payload(), agent_name="c3-rag", session_id="s1", tenant_id="t1"
    )
    await client.aclose()
    assert resp.choices[0].message.content == "ok"
    assert "body" in captured  # the call proceeded


async def test_quota_guard_receives_subject_and_user_header_is_sent() -> None:
    # Enforce-wiring: the client forwards (tenant, project, user) to the guard
    # AND stamps X-User-Id so the ledger attributes per-user usage.
    captured: dict[str, Any] = {}
    seen: dict[str, Any] = {}

    async def guard(tenant_id: str, **kw: Any) -> None:
        seen["tenant_id"] = tenant_id
        seen.update(kw)

    client = _client(captured, quota_guard=guard)
    await client.chat_completion(
        _payload(),
        agent_name="c3-rag",
        session_id="s1",
        tenant_id="t1",
        project_id="p1",
        user_id="u1",
    )
    await client.aclose()
    assert seen == {"tenant_id": "t1", "project_id": "p1", "user_id": "u1"}
    assert captured["headers"]["x-user-id"] == "u1"


async def test_quota_guard_skipped_without_tenant_id() -> None:
    seen: list[str] = []

    async def guard(tenant_id: str, **_kw: Any) -> None:
        seen.append(tenant_id)

    client = _client({}, quota_guard=guard)
    # No tenant_id → the guard is not consulted (no attribution → no enforcement).
    await client.chat_completion(_payload(), agent_name="c3-rag", session_id="s1")
    await client.aclose()
    assert seen == []
