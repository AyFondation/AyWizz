# =============================================================================
# File: test_call_target_router.py
# Path: ay_platform_core/tests/unit/c8_llm/test_call_target_router.py
# Description: Unit tests for the INTERNAL call-target resolver — the endpoint
#              the LiteLLM proxy's credential hook calls to serve callers that
#              reach the proxy directly.
#
#              This endpoint hands out a DECRYPTED provider credential, so its
#              guards are the point of the tests, not an afterthought: it must
#              fail CLOSED when unconfigured, reject a wrong or absent bearer,
#              and never invent a target for an unknown alias.
#
#              The router is mounted on a bare FastAPI app rather than the full
#              c8_admin factory — the factory needs ArangoDB, and none of the
#              behaviour under test involves it.
#
#              Each guard test maps to a clause of R-800-149: not reachable
#              through C1 (structural, asserted in the routers), shared-credential
#              check, fail-closed on an unset credential, and no invented target
#              for an unknown alias.
#
# @relation validates:R-800-149
# =============================================================================

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ay_platform_core.c8_llm.registry.call_target_router import (
    router as call_target_router,
)

pytestmark = pytest.mark.unit

_KEY = "gateway-key-under-test"
_PATH = "/internal/v1/llm/call-target"


class _FakeTarget:
    """Stands in for `CallTarget` — a NamedTuple in the real resolver."""

    def __init__(self, model: str, api_base: str, api_key: str | None) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key


def _app(resolver: Any | None) -> FastAPI:
    app = FastAPI()
    app.include_router(call_target_router)
    if resolver is not None:
        app.state.call_target_provider = resolver
    return app


def _resolver(target: Any) -> Callable[[str], Awaitable[Any]]:
    """Build a resolver that always yields `target` — the shape
    `build_registry_key_provider` returns: an async callable alias → CallTarget."""

    async def _resolve(alias: str) -> Any:
        return target

    return _resolve


@pytest.fixture(autouse=True)
def _gateway_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("C8_GATEWAY_API_KEY", _KEY)


def _post(app: FastAPI, *, token: str | None = _KEY) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return TestClient(app).post(_PATH, json={"alias": "claude-sonnet-5"}, headers=headers)


# ---- Guards ------------------------------------------------------------------


def test_missing_bearer_is_rejected() -> None:
    resp = _post(_app(_resolver(_FakeTarget("anthropic/x", "https://p", "k"))), token=None)
    assert resp.status_code == 401


def test_wrong_bearer_is_rejected() -> None:
    app = _app(_resolver(_FakeTarget("anthropic/x", "https://p", "k")))
    assert _post(app, token="not-the-key").status_code == 401


def test_unset_gateway_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank env var must NOT degrade into an open key-dispensing endpoint."""
    monkeypatch.setenv("C8_GATEWAY_API_KEY", "")
    app = _app(_resolver(_FakeTarget("anthropic/x", "https://p", "k")))
    assert _post(app).status_code == 503


def test_unset_gateway_key_is_checked_before_the_resolver_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must short-circuit: an unconfigured deployment SHALL NOT reach
    the registry, let alone decrypt anything."""
    monkeypatch.setenv("C8_GATEWAY_API_KEY", "")
    called: list[str] = []

    async def _tracking(alias: str) -> Any:
        called.append(alias)
        return _FakeTarget("anthropic/x", "https://p", "k")

    assert _post(_app(_tracking)).status_code == 503
    assert called == []


def test_no_resolver_wired_returns_503() -> None:
    assert _post(_app(None)).status_code == 503


# ---- Resolution --------------------------------------------------------------


def test_unknown_alias_returns_404_rather_than_a_guess() -> None:
    """404 lets the proxy hook leave the request untouched, degrading to the
    proxy's own behaviour instead of routing somewhere invented."""
    resp = _post(_app(_resolver(None)))
    assert resp.status_code == 404


def test_resolved_target_is_returned_in_full() -> None:
    app = _app(
        _resolver(
            _FakeTarget("anthropic/claude-sonnet-5", "https://api.anthropic.com", "sk-x")
        )
    )
    resp = _post(app)
    assert resp.status_code == 200
    assert resp.json() == {
        "model": "anthropic/claude-sonnet-5",
        "api_base": "https://api.anthropic.com",
        "api_key": "sk-x",
    }


def test_target_without_a_stored_key_still_resolves_routing() -> None:
    """No master key / no stored credential still yields model + api_base, so
    the proxy can route while falling back to its own auth."""
    app = _app(_resolver(_FakeTarget("anthropic/claude-sonnet-5", "https://p", None)))
    body = _post(app).json()
    assert body["model"] == "anthropic/claude-sonnet-5"
    assert body["api_key"] is None


def test_the_alias_is_passed_through_verbatim() -> None:
    """The proxy sends `body['model']` as it arrived; the resolver must look up
    exactly that, with no normalisation of its own."""
    seen: list[str] = []

    async def _capture(alias: str) -> Any:
        seen.append(alias)
        return _FakeTarget("anthropic/x", "https://p", "k")

    TestClient(_app(_capture)).post(
        _PATH,
        json={"alias": "litellm_proxy/balanced"},
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert seen == ["litellm_proxy/balanced"]


def test_unknown_body_fields_are_rejected() -> None:
    """`extra="forbid"`: a caller smuggling extra fields is a bug or an attack,
    never something to silently accept on a credential endpoint."""
    resp = TestClient(_app(_resolver(None))).post(
        _PATH,
        json={"alias": "x", "tenant_id": "sneaky"},
        headers={"Authorization": f"Bearer {_KEY}"},
    )
    assert resp.status_code == 422
