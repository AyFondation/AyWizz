# =============================================================================
# File: test_chat_quota_429_e2e.py
# Version: 1
# Path: ay_platform_core/tests/integration/c3_conversation/test_chat_quota_429_e2e.py
# Description: End-to-end quota REACTION on the C3 chat endpoint (gap #4). The C3
#              reply is STREAMING (SSE), so a hard quota block can't become a 429
#              from inside the stream — the service runs an EAGER quota gate
#              before returning the StreamingResponse. This test drives the real
#              `POST /conversations/{id}/messages` endpoint with forward-auth
#              headers and asserts a blocked subject gets a clean 429 (via the
#              app's QuotaExceededError handler), while an under-cap subject is
#              admitted. No real LLM is involved: the block fires before any LLM
#              call, and the under-cap path uses a mocked transport.
#
# @relation validates:R-800-042
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c3_conversation.db.repository import ConversationRepository
from ay_platform_core.c3_conversation.models import ConversationCreate
from ay_platform_core.c3_conversation.router import router as c3_router
from ay_platform_core.c3_conversation.service import ConversationService
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.quota.guard import build_quota_guard
from ay_platform_core.c8_llm.quota.http import register_quota_handler
from ay_platform_core.c8_llm.quota.models import QuotaLimits, QuotaWindow
from ay_platform_core.c8_llm.quota.repository import COLL_CALLS, QuotaRepository
from ay_platform_core.c8_llm.quota.service import QuotaService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_USER = "u1"
_TENANT = "t1"
_HEADERS = {
    "X-User-Id": _USER,
    "X-Tenant-Id": _TENANT,
    "X-User-Roles": "project_editor",
}


@pytest_asyncio.fixture(scope="function")
async def guarded_c3(arango_container: ArangoEndpoint) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c3_q429_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password
    )
    db.create_collection(COLL_CALLS)
    c3_repo = ConversationRepository(db)
    c3_repo._ensure_collections_sync()

    # The LLM is never reached when blocked (eager gate); a mock transport covers
    # the under-cap case. `memory_service` only needs to be truthy to take the
    # RAG path — its retrieval runs lazily inside the stream, after the gate.
    def _mock_sse(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="data: {}\n\n", headers={"content-type": "text/event-stream"}
        )

    transport = httpx.MockTransport(_mock_sse)
    llm = LLMGatewayClient(
        ClientSettings(gateway_url="http://mock/v1"),
        bearer_token="t",
        http_client=httpx.AsyncClient(transport=transport, base_url="http://mock/v1"),
        quota_guard=build_quota_guard(db),
    )
    service = ConversationService(c3_repo, memory_service=MagicMock(), llm_client=llm)
    app = FastAPI()
    app.include_router(c3_router)
    register_quota_handler(app)
    app.state.conversation_service = service

    conv = await service.create_conversation(
        _USER, ConversationCreate(title="q", project_id="p1")
    )
    try:
        yield {"app": app, "db": db, "conversation_id": conv.id}
    finally:
        await llm.aclose()
        cleanup_arango_database(arango_container, db_name)


def _seed_over_cap(db: Any) -> None:
    db.collection(COLL_CALLS).insert(
        {
            "_key": uuid.uuid4().hex,
            "tags": {"tenant_id": _TENANT, "user_id": _USER, "session_id": "s"},
            "timestamp_start": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "cost_usd": 99.0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )


async def _set_user_cap(db: Any, cap: float) -> None:
    await QuotaService(QuotaRepository(db)).set_policy(
        [
            QuotaWindow(
                key="week",
                label="Week",
                duration_seconds=7 * 86400,
                anchor="calendar_week",
                limits={"user": QuotaLimits(max_cost_usd=cap)},
            )
        ]
    )


async def test_chat_endpoint_returns_429_when_quota_blocked(
    guarded_c3: dict[str, Any],
) -> None:
    db, app, cid = guarded_c3["db"], guarded_c3["app"], guarded_c3["conversation_id"]
    await _set_user_cap(db, 10.0)
    _seed_over_cap(db)  # $99 > $10 user cap
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://c3") as c:
        resp = await c.post(
            f"/api/v1/conversations/{cid}/messages",
            headers=_HEADERS,
            json={"content": "hi"},
        )
    assert resp.status_code == 429
    assert resp.json()["quota"]["blocked"] is True


async def test_chat_endpoint_admits_under_cap(guarded_c3: dict[str, Any]) -> None:
    db, app, cid = guarded_c3["db"], guarded_c3["app"], guarded_c3["conversation_id"]
    await _set_user_cap(db, 1000.0)  # generous — the eager gate passes
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://c3") as c:
        resp = await c.post(
            f"/api/v1/conversations/{cid}/messages",
            headers=_HEADERS,
            json={"content": "hi"},
        )
    assert resp.status_code == 200  # not blocked → the SSE stream begins
