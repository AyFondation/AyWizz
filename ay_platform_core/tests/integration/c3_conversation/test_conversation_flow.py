# =============================================================================
# File: test_conversation_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c3_conversation/test_conversation_flow.py
# Description: Integration tests for C3 — full CRUD cycle and SSE stream,
#              backed by a real ArangoDB instance via testcontainers.
#              Sync fixtures, async test methods (asyncio_mode=auto).
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.integration

_USER = "test-user-c3"
_HEADERS = {"X-User-Id": _USER}


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Create & list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Integration test"},
            headers=_HEADERS,
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["conversation"]["title"] == "Integration test"
    assert data["conversation"]["owner_id"] == _USER


@pytest.mark.asyncio
async def test_list_conversations_empty_initially(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        resp = await client.get("/api/v1/conversations", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["conversations"] == []


@pytest.mark.asyncio
async def test_list_conversations_after_create(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        await client.post(
            "/api/v1/conversations",
            json={"title": "First"},
            headers=_HEADERS,
        )
        await client.post(
            "/api/v1/conversations",
            json={"title": "Second"},
            headers=_HEADERS,
        )
        resp = await client.get("/api/v1/conversations", headers=_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()["conversations"]) == 2


# ---------------------------------------------------------------------------
# Get / update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations",
            json={"title": "GetMe"},
            headers=_HEADERS,
        )
        conv_id = create_resp.json()["conversation"]["id"]
        get_resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=_HEADERS)
    assert get_resp.status_code == 200
    assert get_resp.json()["conversation"]["id"] == conv_id


@pytest.mark.asyncio
async def test_get_conversation_wrong_user_returns_403(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Private"},
            headers=_HEADERS,
        )
        conv_id = create_resp.json()["conversation"]["id"]
        resp = await client.get(
            f"/api/v1/conversations/{conv_id}",
            headers={"X-User-Id": "other-user"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_conversation_title(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations",
            json={"title": "Old title"},
            headers=_HEADERS,
        )
        conv_id = create_resp.json()["conversation"]["id"]
        patch_resp = await client.patch(
            f"/api/v1/conversations/{conv_id}",
            json={"title": "New title"},
            headers=_HEADERS,
        )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["conversation"]["title"] == "New title"


@pytest.mark.asyncio
async def test_delete_conversation(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations",
            json={"title": "ToDelete"},
            headers=_HEADERS,
        )
        conv_id = create_resp.json()["conversation"]["id"]
        del_resp = await client.delete(
            f"/api/v1/conversations/{conv_id}", headers=_HEADERS
        )
        assert del_resp.status_code == 204
        get_resp = await client.get(
            f"/api/v1/conversations/{conv_id}", headers=_HEADERS
        )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_deleted_conversation_not_in_list(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations", json={"title": "Ghost"}, headers=_HEADERS
        )
        conv_id = create_resp.json()["conversation"]["id"]
        await client.delete(f"/api/v1/conversations/{conv_id}", headers=_HEADERS)
        list_resp = await client.get("/api/v1/conversations", headers=_HEADERS)
    assert all(
        c["id"] != conv_id for c in list_resp.json()["conversations"]
    )


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_empty_on_new_conversation(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations", json={"title": "Msgs"}, headers=_HEADERS
        )
        conv_id = create_resp.json()["conversation"]["id"]
        resp = await client.get(
            f"/api/v1/conversations/{conv_id}/messages", headers=_HEADERS
        )
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


@pytest.mark.asyncio
async def test_send_message_streams_sse(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations", json={"title": "Chat"}, headers=_HEADERS
        )
        conv_id = create_resp.json()["conversation"]["id"]
        stream_resp = await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            json={"content": "Hello C3"},
            headers=_HEADERS,
        )
    assert stream_resp.status_code == 200
    assert "text/event-stream" in stream_resp.headers.get("content-type", "")
    body = stream_resp.text
    assert "data: " in body
    assert "[DONE]" in body


@pytest.mark.asyncio
async def test_send_message_persists_both_roles(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations", json={"title": "Persist"}, headers=_HEADERS
        )
        conv_id = create_resp.json()["conversation"]["id"]
        await client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            json={"content": "test message"},
            headers=_HEADERS,
        )
        msgs_resp = await client.get(
            f"/api/v1/conversations/{conv_id}/messages", headers=_HEADERS
        )
    messages = msgs_resp.json()["messages"]
    assert len(messages) == 2
    roles = {m["role"] for m in messages}
    assert "user" in roles
    assert "assistant" in roles


# ---------------------------------------------------------------------------
# Expert mode events stub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expert_mode_events_returns_unavailable(conv_app: FastAPI) -> None:
    async with _client(conv_app) as client:
        create_resp = await client.post(
            "/api/v1/conversations", json={"title": "Expert"}, headers=_HEADERS
        )
        conv_id = create_resp.json()["conversation"]["id"]
        resp = await client.get(
            f"/api/v1/conversations/{conv_id}/events", headers=_HEADERS
        )
    assert resp.status_code == 200
    assert "unavailable" in resp.text
