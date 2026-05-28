# =============================================================================
# File: test_remote_service.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_remote_service.py
# Description: Unit tests for `RemoteMemoryService` (c7_memory/remote.py).
#              The class is a thin HTTP wrapper; tests pin its wire
#              format (URL, headers, body) and response parsing without
#              standing up a real C7. httpx is mocked via a custom
#              transport so the assertions are exact.
#
# @relation validates:R-100-114
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ay_platform_core.c7_memory.models import (
    IndexKind,
    RetrievalRequest,
    RetrievalResponse,
)
from ay_platform_core.c7_memory.remote import RemoteMemoryService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio(loop_scope="function")]


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """httpx transport that captures every outgoing request and replies
    with a fixed JSON body. Lets the test inspect headers / body exactly
    rather than rely on a real server."""

    def __init__(self, response_body: dict[str, Any], status_code: int = 200) -> None:
        self.captured: list[httpx.Request] = []
        self._body = response_body
        self._status = status_code

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.captured.append(request)
        return httpx.Response(
            status_code=self._status,
            json=self._body,
            request=request,
        )


def _canned_retrieval_response() -> dict[str, Any]:
    """Minimum payload that satisfies `RetrievalResponse.model_validate`."""
    return {
        "retrieval_id": "ret-test-1",
        "request": {
            "project_id": "p1",
            "query": "anything",
            "indexes": ["external_sources"],
            "top_k": 5,
            "weights": None,
            "filters": {},
            "include_history": False,
            "include_deprecated": False,
        },
        "hits": [],
        "latency_ms": 7,
    }


# ---------------------------------------------------------------------------
# retrieve()
# ---------------------------------------------------------------------------


async def test_retrieve_posts_to_correct_url_with_forward_auth_headers() -> None:
    """The wire contract: POST <base>/api/v1/memory/retrieve with the
    forward-auth header triplet that mirrors what Traefik would inject
    after C2 verify."""
    transport = _ScriptedTransport(_canned_retrieval_response())
    http = httpx.AsyncClient(transport=transport)
    remote = RemoteMemoryService("http://c7-memory.aywizz:8000", http_client=http)

    payload = RetrievalRequest(
        project_id="p1",
        query="rocket fuel",
        indexes=[IndexKind.EXTERNAL_SOURCES],
        top_k=5,
    )
    response = await remote.retrieve(
        payload,
        tenant_id="tenant-x",
        user_id="alice",
        user_roles="project_editor",
    )

    assert isinstance(response, RetrievalResponse)
    assert response.retrieval_id == "ret-test-1"

    assert len(transport.captured) == 1
    sent = transport.captured[0]
    assert sent.method == "POST"
    assert str(sent.url) == "http://c7-memory.aywizz:8000/api/v1/memory/retrieve"
    assert sent.headers["X-User-Id"] == "alice"
    assert sent.headers["X-Tenant-Id"] == "tenant-x"
    assert sent.headers["X-User-Roles"] == "project_editor"
    assert sent.headers["Content-Type"] == "application/json"


async def test_retrieve_serialises_indexes_as_strings() -> None:
    """`mode='json'` SHALL serialise `IndexKind` as its string value
    (`external_sources`), not as the enum repr — otherwise FastAPI on
    the receiving end fails to validate the body."""
    transport = _ScriptedTransport(_canned_retrieval_response())
    http = httpx.AsyncClient(transport=transport)
    remote = RemoteMemoryService("http://c7", http_client=http)

    payload = RetrievalRequest(
        project_id="p1",
        query="anything",
        indexes=[IndexKind.EXTERNAL_SOURCES, IndexKind.CONVERSATIONS],
        top_k=3,
    )
    await remote.retrieve(
        payload, tenant_id="t", user_id="u", user_roles="project_editor",
    )

    sent = transport.captured[0]
    body = sent.read().decode()
    assert '"external_sources"' in body
    assert '"conversations"' in body


async def test_retrieve_raises_on_non_2xx() -> None:
    """A 4xx/5xx from C7 SHALL surface as an httpx.HTTPStatusError —
    the caller decides whether to fall back to no-context or surface
    an error."""
    transport = _ScriptedTransport(
        {"detail": "tenant scope violation"}, status_code=403,
    )
    http = httpx.AsyncClient(transport=transport)
    remote = RemoteMemoryService("http://c7", http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        await remote.retrieve(
            RetrievalRequest(
                project_id="p1",
                query="q",
                indexes=[IndexKind.EXTERNAL_SOURCES],
                top_k=3,
            ),
            tenant_id="t",
            user_id="u",
            user_roles="project_editor",
        )


# ---------------------------------------------------------------------------
# auth header validation
# ---------------------------------------------------------------------------


async def test_retrieve_rejects_empty_user_id() -> None:
    remote = RemoteMemoryService("http://c7")
    with pytest.raises(ValueError, match="user_id is required"):
        await remote.retrieve(
            RetrievalRequest(
                project_id="p", query="q",
                indexes=[IndexKind.EXTERNAL_SOURCES], top_k=3,
            ),
            tenant_id="t",
            user_id="",
            user_roles="project_editor",
        )
    await remote.aclose()


async def test_retrieve_rejects_empty_tenant_id() -> None:
    remote = RemoteMemoryService("http://c7")
    with pytest.raises(ValueError, match="tenant_id is required"):
        await remote.retrieve(
            RetrievalRequest(
                project_id="p", query="q",
                indexes=[IndexKind.EXTERNAL_SOURCES], top_k=3,
            ),
            tenant_id="",
            user_id="u",
            user_roles="project_editor",
        )
    await remote.aclose()


async def test_constructor_rejects_empty_base_url() -> None:
    with pytest.raises(ValueError, match="base_url is required"):
        RemoteMemoryService("")


# ---------------------------------------------------------------------------
# ingest_conversation_turn — stubbed in v1
# ---------------------------------------------------------------------------


async def test_ingest_conversation_turn_raises_not_implemented() -> None:
    """Phase E (conversation memory loop) is disabled in remote mode
    pending a dedicated C7 endpoint. The caller (C3._rag_stream) wraps
    this in `contextlib.suppress(Exception)`, so the chat reply is
    unaffected — the test pins the contract, not a regression."""
    remote = RemoteMemoryService("http://c7")
    with pytest.raises(NotImplementedError, match="conversation memory loop"):
        await remote.ingest_conversation_turn(
            tenant_id="t",
            project_id="p",
            conversation_id="c",
            turn_id="x",
            user_message="hi",
            assistant_reply="hello",
            actor_id="alice",
        )
    await remote.aclose()


# ---------------------------------------------------------------------------
# Resource ownership — close behaviour
# ---------------------------------------------------------------------------


async def test_owns_internal_client_and_closes_it() -> None:
    """When no client is injected, the service constructs and OWNS one;
    `aclose()` closes it. After close, further calls SHALL raise."""
    remote = RemoteMemoryService("http://c7")
    await remote.aclose()
    with pytest.raises(RuntimeError):
        await remote._http.get("http://c7/anything")


async def test_does_not_close_injected_client() -> None:
    """When a shared client is injected, `aclose()` SHALL NOT close it
    — the caller manages the client's lifecycle."""
    transport = _ScriptedTransport(_canned_retrieval_response())
    shared = httpx.AsyncClient(transport=transport)
    remote = RemoteMemoryService("http://c7", http_client=shared)
    await remote.aclose()
    # Shared client is still usable.
    await shared.post(
        "http://c7/api/v1/memory/retrieve",
        json={
            "project_id": "p1",
            "query": "x",
            "indexes": ["external_sources"],
            "top_k": 1,
        },
        headers={"X-User-Id": "u", "X-Tenant-Id": "t"},
    )
    await shared.aclose()
