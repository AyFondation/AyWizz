# =============================================================================
# File: test_model_resolver.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_model_resolver.py
# Description: Unit tests for the PROVIDER-INDEPENDENT model resolution (D-011).
#              `_resolve_model_via_catalog` maps an agent → a quality tier and
#              returns the project's model of that quality FROM THE REGISTRY
#              CATALOGUE (falling back across qualities), and the C8 client's
#              `_resolve_model_alias` sets `body['model']` from it when no model
#              was otherwise chosen — so registering + associating a model in the
#              HMI is enough, NO model/provider name in config.
#
# @relation validates:R-800-146
# =============================================================================

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.registry.key_provider import _resolve_model_via_catalog
from ay_platform_core.c8_llm.registry.models import ModelQuality

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeCatalog:
    """Records resolve() calls; returns an alias per requested quality."""

    def __init__(self, by_quality: dict[ModelQuality, str | None]) -> None:
        self._by = by_quality
        self.calls: list[tuple[ModelQuality, str | None, bool]] = []

    async def resolve(
        self,
        tenant_id: str,
        quality: ModelQuality,
        *,
        project_id: str | None = None,
        require_tool_calling: bool = False,
    ) -> Any:
        self.calls.append((quality, project_id, require_tool_calling))
        alias = self._by.get(quality)
        return SimpleNamespace(model_alias=alias) if alias else None


# --- _resolve_model_via_catalog --------------------------------------------


async def test_flagship_agent_tries_high_first() -> None:
    cat = _FakeCatalog({ModelQuality.HIGH: "opus-x"})
    alias = await _resolve_model_via_catalog(
        cat, "architect", "t1", "p1", require_tool_calling=False
    )
    assert alias == "opus-x"
    assert cat.calls[0][0] is ModelQuality.HIGH  # HIGH tried first


async def test_leaf_agent_tries_low_first() -> None:
    cat = _FakeCatalog({ModelQuality.LOW: "haiku-x"})
    alias = await _resolve_model_via_catalog(
        cat, "sub-agent", "t1", "p1", require_tool_calling=False
    )
    assert alias == "haiku-x"
    assert cat.calls[0][0] is ModelQuality.LOW


async def test_falls_back_across_qualities() -> None:
    # architect prefers HIGH, but the project only has a LOW model → still used.
    cat = _FakeCatalog({ModelQuality.LOW: "only-model"})
    alias = await _resolve_model_via_catalog(
        cat, "architect", "t1", "p1", require_tool_calling=False
    )
    assert alias == "only-model"
    # It tried HIGH, MEDIUM, then LOW.
    assert [c[0] for c in cat.calls] == [
        ModelQuality.HIGH, ModelQuality.MEDIUM, ModelQuality.LOW
    ]


async def test_default_agent_tries_medium_first() -> None:
    cat = _FakeCatalog({ModelQuality.MEDIUM: "sonnet-x"})
    alias = await _resolve_model_via_catalog(
        cat, "c3-rag", "t1", "p1", require_tool_calling=False
    )
    assert alias == "sonnet-x"
    assert cat.calls[0][0] is ModelQuality.MEDIUM


async def test_no_tenant_returns_none_without_calling() -> None:
    cat = _FakeCatalog({ModelQuality.MEDIUM: "x"})
    out = await _resolve_model_via_catalog(cat, "c3-rag", None, "p1", require_tool_calling=False)
    assert out is None
    assert cat.calls == []


async def test_no_model_at_any_quality_returns_none() -> None:
    cat = _FakeCatalog({})
    out = await _resolve_model_via_catalog(cat, "c3-rag", "t1", "p1", require_tool_calling=False)
    assert out is None
    assert len(cat.calls) == 3  # tried all three qualities


async def test_require_tool_calling_and_project_are_forwarded() -> None:
    cat = _FakeCatalog({ModelQuality.MEDIUM: "m"})
    await _resolve_model_via_catalog(cat, "c3-docgen", "t1", "proj9", require_tool_calling=True)
    assert cat.calls[0] == (ModelQuality.MEDIUM, "proj9", True)


async def test_catalog_error_is_swallowed() -> None:
    class _Boom:
        async def resolve(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("db down")

    out = await _resolve_model_via_catalog(
        _Boom(), "c3-rag", "t1", "p1", require_tool_calling=False
    )
    assert out is None


# --- client _resolve_model_alias -------------------------------------------


def _client(model_provider: Any) -> LLMGatewayClient:
    return LLMGatewayClient(
        ClientSettings(gateway_url="http://test/v1"),
        model_provider=model_provider,
    )


async def test_client_sets_model_from_resolver_when_empty() -> None:
    async def provider(agent: str, tenant: str | None, project: str | None, **_: Any) -> str | None:
        return "flagship"

    body: dict[str, Any] = {"messages": []}  # no model
    await _client(provider)._resolve_model_alias(
        body, agent_name="c3-rag", tenant_id="t1", project_id="p1"
    )
    assert body["model"] == "flagship"


async def test_client_does_not_override_an_existing_model() -> None:
    async def provider(*_a: Any, **_k: Any) -> str | None:
        return "flagship"

    body: dict[str, Any] = {"model": "explicit", "messages": []}
    await _client(provider)._resolve_model_alias(
        body, agent_name="c3-rag", tenant_id="t1", project_id="p1"
    )
    assert body["model"] == "explicit"  # explicit choice wins


async def test_client_forwards_require_tool_calling_from_body_tools() -> None:
    seen: dict[str, Any] = {}

    async def provider(
        agent: str, tenant: str | None, project: str | None, *, require_tool_calling: bool
    ) -> str | None:
        seen["rtc"] = require_tool_calling
        return "m"

    body: dict[str, Any] = {"messages": [], "tools": [{"type": "function"}]}
    await _client(provider)._resolve_model_alias(
        body, agent_name="c3-docgen", tenant_id="t1", project_id="p1"
    )
    assert seen["rtc"] is True


async def test_client_no_provider_is_noop() -> None:
    body: dict[str, Any] = {"messages": []}
    await _client(None)._resolve_model_alias(
        body, agent_name="c3-rag", tenant_id="t1", project_id="p1"
    )
    assert "model" not in body
