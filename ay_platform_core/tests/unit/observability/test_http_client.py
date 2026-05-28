# =============================================================================
# File: test_http_client.py
# Version: 1
# Path: ay_platform_core/tests/unit/observability/test_http_client.py
# Description: `make_traced_client` injects the current `traceparent` on
#              every outgoing request via the httpx event hook.
# =============================================================================

from __future__ import annotations

import asyncio
import contextvars

import httpx
import pytest

from ay_platform_core.observability.context import (
    TraceContext,
    set_trace_context,
)
from ay_platform_core.observability.http_client import make_traced_client

pytestmark = pytest.mark.unit


@pytest.fixture(scope="function")
def trace_active() -> TraceContext:
    ctx = TraceContext(
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="fedcba9876543210",
        sampled=True,
    )
    set_trace_context(ctx)
    return ctx


@pytest.mark.asyncio
async def test_outgoing_request_carries_current_traceparent(
    trace_active: TraceContext,
) -> None:
    captured: list[str | None] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("traceparent"))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_handler)
    async with make_traced_client(
        transport=transport, base_url="http://test.local"
    ) as client:
        await client.get("/x")

    assert len(captured) == 1
    out = captured[0]
    assert out is not None
    # Same trace + span as the active context, sampled flag preserved.
    assert out == f"00-{trace_active.trace_id}-{trace_active.span_id}-01"


@pytest.mark.asyncio
async def test_no_active_context_no_header_injected() -> None:
    """When no request scope is active (background task, startup), the
    factory leaves the request alone — better than emitting an empty
    traceparent that downstream parsers would treat as malformed."""
    captured: list[str | None] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("traceparent"))
        return httpx.Response(200, json={"ok": True})

    async def _under_clean_context() -> None:
        transport = httpx.MockTransport(_handler)
        async with make_traced_client(
            transport=transport, base_url="http://test.local"
        ) as client:
            await client.get("/x")

    # Run in a fresh context where the trace ContextVars are unset.
    new_ctx = contextvars.Context()
    fut = asyncio.get_event_loop().create_task(
        _under_clean_context(), context=new_ctx
    )
    await fut

    assert len(captured) == 1
    assert captured[0] is None


@pytest.mark.asyncio
async def test_caller_set_traceparent_is_preserved(
    trace_active: TraceContext,
) -> None:
    """If the caller already sets traceparent explicitly, the hook does
    NOT overwrite it. Rare but supported (e.g. forwarding a parent's
    trace through a relay)."""
    captured: list[str | None] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("traceparent"))
        return httpx.Response(200)

    transport = httpx.MockTransport(_handler)
    forced = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
    async with make_traced_client(
        transport=transport, base_url="http://test.local"
    ) as client:
        await client.get("/x", headers={"traceparent": forced})

    assert captured == [forced]


@pytest.mark.asyncio
async def test_caller_supplied_event_hooks_are_kept(
    trace_active: TraceContext,
) -> None:
    """Passing `event_hooks=` to make_traced_client merges with the
    auto-injected hook — never replaces it."""
    seen: list[str] = []

    async def _user_hook(request: httpx.Request) -> None:
        seen.append("user-hook-fired")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    transport = httpx.MockTransport(_handler)
    async with make_traced_client(
        transport=transport,
        base_url="http://test.local",
        event_hooks={"request": [_user_hook]},
    ) as client:
        await client.get("/x")

    assert seen == ["user-hook-fired"]
