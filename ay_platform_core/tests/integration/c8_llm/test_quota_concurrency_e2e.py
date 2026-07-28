# =============================================================================
# File: test_quota_concurrency_e2e.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_llm/test_quota_concurrency_e2e.py
# Description: Concurrency tests for the quota subsystem. The quota guard is
#              READ-mostly (it sums the ledger), with one WRITE path — the
#              first-use session anchor (`advance=True`). These tests run real
#              concurrent operations against a real ArangoDB to verify: (1) the
#              session anchor opens without corruption under a race of first
#              calls, (2) concurrent cost-receiver writes are ALL counted (no
#              lost writes), and (3) concurrent guarded calls enforce a cap
#              CONSISTENTLY (no flapping). No real LLM is invoked.
#
# @relation validates:R-800-042
# =============================================================================

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.main import CostReceiverConfig, create_app
from ay_platform_core.c8_llm.models import ChatCompletionRequest, ChatMessage, ChatRole
from ay_platform_core.c8_llm.quota.guard import QuotaExceededError, build_quota_guard
from ay_platform_core.c8_llm.quota.models import QuotaLimits, QuotaWindow
from ay_platform_core.c8_llm.quota.repository import COLL_CALLS, QuotaRepository
from ay_platform_core.c8_llm.quota.service import QuotaService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_CANONICAL_CONFIG = (
    Path(__file__).resolve().parents[4]
    / "infra"
    / "c8_gateway"
    / "config"
    / "litellm-config.yaml"
)


@pytest_asyncio.fixture(scope="function")
async def quota_db(arango_container: ArangoEndpoint) -> AsyncIterator[Any]:
    db_name = f"c8_qconc_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    db.create_collection(COLL_CALLS)
    try:
        yield db
    finally:
        cleanup_arango_database(arango_container, db_name)


async def _set_policy(db: Any, *windows: QuotaWindow) -> None:
    await QuotaService(QuotaRepository(db)).set_policy(list(windows))


def _week(**limits: QuotaLimits) -> QuotaWindow:
    return QuotaWindow(
        key="week",
        label="Week",
        duration_seconds=7 * 86400,
        anchor="calendar_week",
        limits={k: v for k, v in limits.items()},  # type: ignore[misc]
    )


def _seed(db: Any, *, cost: float, tenant: str = "t1", user: str | None = None) -> None:
    tags: dict[str, str] = {"tenant_id": tenant, "session_id": "s"}
    if user is not None:
        tags["user_id"] = user
    db.collection(COLL_CALLS).insert(
        {
            "_key": uuid.uuid4().hex,
            "tags": tags,
            "timestamp_start": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "cost_usd": cost,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )


def _client(db: Any) -> LLMGatewayClient:
    def _ok(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "m",
                "object": "chat.completion",
                "created": 1,
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

    http = httpx.AsyncClient(transport=httpx.MockTransport(_ok), base_url="http://c8/v1")
    return LLMGatewayClient(
        ClientSettings(gateway_url="http://c8/v1"),
        bearer_token="t",
        http_client=http,
        quota_guard=build_quota_guard(db),
    )


def _payload() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="claude-haiku-fast",
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
    )


# --- 1. Session-anchor race: concurrent first calls don't corrupt -----------


async def test_concurrent_session_open_is_consistent(quota_db: Any) -> None:
    session = QuotaWindow(
        key="session",
        label="Session",
        duration_seconds=5 * 3600,
        anchor="first_use",
        limits={"user": QuotaLimits(max_cost_usd=1000.0)},
    )
    await _set_policy(quota_db, session)
    svc = QuotaService(QuotaRepository(quota_db))
    # 20 concurrent "first calls" for the same user, all opening the session.
    results = await asyncio.gather(
        *(svc.evaluate("t1", user_id="u1", advance=True) for _ in range(20))
    )
    assert all(not s.blocked for s in results)  # under the $1000 cap, none block
    # Exactly one anchor exists and the window reports an active session.
    anchor = await QuotaRepository(quota_db).get_anchor("session:user:u1")
    assert anchor is not None
    assert all(s.windows[0].seconds_until_reset is not None for s in results)


# --- 2. Concurrent ledger writes are ALL counted (no lost writes) -----------


@pytest_asyncio.fixture(scope="function")
async def receiver(arango_container: ArangoEndpoint) -> AsyncIterator[tuple[FastAPI, Any]]:
    db_name = f"c8_qconc_recv_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    cfg = CostReceiverConfig(
        arango_url=arango_container.url,
        arango_db=db_name,
        arango_username="root",
        arango_password=arango_container.password,
        litellm_config_path=str(_CANONICAL_CONFIG),
    )
    app = create_app(cfg)
    app.state.cost_sink.ensure_collection()
    db = client.db(db_name, username="root", password=arango_container.password)
    try:
        yield app, db
    finally:
        cleanup_arango_database(arango_container, db_name)


async def test_concurrent_receiver_writes_all_counted(
    receiver: tuple[FastAPI, Any],
) -> None:
    app, db = receiver
    await _set_policy(db, _week())  # calendar window → deterministic sum
    now = datetime.now(UTC)
    envelope = {
        "status": "success",
        "model": "claude-haiku-fast",
        "usage": {"prompt_tokens": 100, "completion_tokens": 0, "cached_tokens": 0},
        "headers": {
            "X-Agent-Name": "a",
            "X-Session-Id": "s",
            "X-Tenant-Id": "t1",
            "X-User-Id": "u1",
        },
        "fingerprint": "f",
        "start_time": (now - timedelta(seconds=2)).isoformat(),
        "end_time": now.isoformat(),
    }
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://r") as c:
        resps = await asyncio.gather(
            *(c.post("/internal/llm-calls", json=envelope) for _ in range(30))
        )
    assert all(r.status_code == 200 for r in resps)
    # No lost writes: 30 rows, and the quota sums all 3000 tokens for the user.
    assert db.collection(COLL_CALLS).count() == 30
    status = await QuotaService(QuotaRepository(db)).evaluate("t1", user_id="u1")
    user_level = next(s for s in status.windows[0].levels if s.level == "user")
    assert user_level.usage_tokens == 3000


# --- 3. Concurrent guarded calls enforce a cap consistently -----------------


async def test_concurrent_calls_over_cap_all_blocked(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(user=QuotaLimits(max_cost_usd=10.0)))
    _seed(quota_db, cost=12.0, tenant="t1", user="u1")  # already over $10
    client = _client(quota_db)

    async def _one() -> bool:
        try:
            await client.chat_completion(
                _payload(), agent_name="c3-rag", session_id="s", tenant_id="t1", user_id="u1"
            )
            return False  # not blocked
        except QuotaExceededError:
            return True

    try:
        verdicts = await asyncio.gather(*(_one() for _ in range(15)))
    finally:
        await client.aclose()
    assert all(verdicts)  # every concurrent call is blocked — no flapping


async def test_concurrent_calls_under_cap_all_pass(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(user=QuotaLimits(max_cost_usd=100.0)))
    _seed(quota_db, cost=1.0, tenant="t1", user="u1")  # well under $100
    client = _client(quota_db)

    async def _one() -> bool:
        try:
            await client.chat_completion(
                _payload(), agent_name="c3-rag", session_id="s", tenant_id="t1", user_id="u1"
            )
            return True
        except QuotaExceededError:
            return False

    try:
        verdicts = await asyncio.gather(*(_one() for _ in range(15)))
    finally:
        await client.aclose()
    assert all(verdicts)  # every concurrent call proceeds
