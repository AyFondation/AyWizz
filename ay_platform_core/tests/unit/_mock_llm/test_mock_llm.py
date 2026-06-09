# =============================================================================
# File: test_mock_llm.py
# Version: 1
# Path: ay_platform_core/tests/unit/_mock_llm/test_mock_llm.py
# Description: Behavioural tests for the scripted mock LLM proxy
#              (`_mock_llm.main`). It is a test scaffold the system tests rely
#              on, so its own behaviour SHALL be validated: the admin queue
#              (enqueue/reset/calls), the OpenAI-compatible completions endpoint
#              (queued envelope vs BLOCKED fallback), the call log, and the
#              auth/tag guards (401 without bearer, 400 without agent/session).
#              A mock that silently misbehaves would weaken every test that
#              drives a component through it.
# =============================================================================

from __future__ import annotations

import json

import httpx
import pytest

from ay_platform_core._mock_llm.main import create_app

pytestmark = pytest.mark.asyncio

_AUTH = {
    "Authorization": "Bearer test-token",
    "X-Agent-Name": "planner",
    "X-Session-Id": "s1",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
        base_url="http://mock",
    )


async def test_health() -> None:
    async with _client() as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["component"] == "_mock_llm"


async def test_enqueue_then_completion_returns_envelope_and_logs_call() -> None:
    envelope = {"status": "DONE", "output": {"answer": 42}}
    async with _client() as c:
        q = await c.post("/admin/enqueue", json={"envelope": envelope})
        assert q.status_code == 200
        assert q.json() == {"queued": 1}

        resp = await c.post(
            "/v1/chat/completions",
            headers=_AUTH,
            json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert json.loads(content) == envelope

        calls = await c.get("/admin/calls")
    assert len(calls.json()) == 1
    assert calls.json()[0]["model"] == "mock"


async def test_empty_queue_returns_blocked() -> None:
    async with _client() as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers=_AUTH,
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    envelope = json.loads(resp.json()["choices"][0]["message"]["content"])
    assert envelope["status"] == "BLOCKED"
    assert "queue empty" in envelope["blocker"]["reason"]


async def test_reset_clears_queue_and_calls() -> None:
    async with _client() as c:
        await c.post("/admin/enqueue", json={"envelope": {"status": "DONE"}})
        await c.post(
            "/v1/chat/completions",
            headers=_AUTH,
            json={"messages": []},
        )
        reset = await c.post("/admin/reset")
        assert reset.json() == {"status": "reset"}
        calls = await c.get("/admin/calls")
    assert calls.json() == []


async def test_missing_bearer_is_401() -> None:
    async with _client() as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"X-Agent-Name": "planner", "X-Session-Id": "s1"},
            json={"messages": []},
        )
    assert resp.status_code == 401


async def test_missing_tags_is_400() -> None:
    async with _client() as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": []},
        )
    assert resp.status_code == 400
