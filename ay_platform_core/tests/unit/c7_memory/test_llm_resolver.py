# =============================================================================
# File: test_llm_resolver.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_llm_resolver.py
# Description: Unit tests for the C7 → c8_admin model-quality resolver. The
#              load-bearing property is GRACEFUL DEGRADATION: a disabled
#              resolver, a 404 (no model of that quality), a non-200, or a
#              transport error ALL yield None so ingestion never breaks on a
#              governance lookup. Also verifies the request shape (path, query,
#              forward-auth headers).
# =============================================================================

from __future__ import annotations

import httpx
import pytest

from ay_platform_core.c7_memory.llm_resolver import LLMResolverClient

pytestmark = pytest.mark.asyncio


def _client(handler: object) -> LLMResolverClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return LLMResolverClient(
        base_url="http://c8-admin:8000",
        client=httpx.AsyncClient(transport=transport, base_url="http://c8-admin:8000"),
    )


async def test_disabled_when_base_url_empty() -> None:
    resolver = LLMResolverClient(base_url="")
    assert resolver.enabled is False
    assert (
        await resolver.resolve(
            tenant_id="t1", user_id="u1", model_quality="low"
        )
        is None
    )
    await resolver.aclose()


async def test_resolve_returns_alias_and_sends_expected_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["quality"] = request.url.params.get("model_quality")
        seen["vision"] = request.url.params.get("require_vision")
        seen["x_user"] = request.headers.get("X-User-Id")
        seen["x_tenant"] = request.headers.get("X-Tenant-Id")
        return httpx.Response(200, json={"model_alias": "claude-haiku-fast"})

    resolver = _client(handler)
    alias = await resolver.resolve(
        tenant_id="t1", user_id="u1", model_quality="low", require_vision=True
    )
    await resolver.aclose()
    assert alias == "claude-haiku-fast"
    assert seen["path"] == "/api/v1/llm/catalog/resolve"
    assert seen["quality"] == "low"
    assert seen["vision"] == "true"
    assert seen["x_user"] == "u1"
    assert seen["x_tenant"] == "t1"


async def test_resolve_404_is_none_not_error() -> None:
    resolver = _client(lambda req: httpx.Response(404, json={"detail": "no model"}))
    assert (
        await resolver.resolve(tenant_id="t1", user_id="u1", model_quality="high")
        is None
    )
    await resolver.aclose()


async def test_resolve_non_200_is_none() -> None:
    resolver = _client(lambda req: httpx.Response(500, text="boom"))
    assert (
        await resolver.resolve(tenant_id="t1", user_id="u1", model_quality="low")
        is None
    )
    await resolver.aclose()


async def test_resolve_transport_error_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    resolver = _client(handler)
    assert (
        await resolver.resolve(tenant_id="t1", user_id="u1", model_quality="low")
        is None
    )
    await resolver.aclose()


async def test_resolve_malformed_body_is_none() -> None:
    resolver = _client(lambda req: httpx.Response(200, json={"unexpected": 1}))
    assert (
        await resolver.resolve(tenant_id="t1", user_id="u1", model_quality="low")
        is None
    )
    await resolver.aclose()
