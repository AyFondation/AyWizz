# =============================================================================
# File: test_quota_admin_api.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_admin/test_quota_admin_api.py
# Description: Integration tests for the global LLM quota admin API (Lot 3):
#              GET/PUT the single policy and GET per-tenant status, evaluated
#              against real `llm_calls` rows in ArangoDB. Covers the role gate,
#              the policy round-trip, and a real exceeded-window verdict.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.quota.repository import (
    COLL_CALLS,
    QuotaRepository,
)
from ay_platform_core.c8_llm.quota.router import router as quota_router
from ay_platform_core.c8_llm.quota.service import QuotaService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TMGR_HEADERS = {"X-User-Id": "u-tmgr", "X-User-Roles": "tenant_manager"}
_USER_HEADERS = {"X-User-Id": "u-plain", "X-User-Roles": "user"}


def _call_doc(tenant_id: str, cost: float, in_tok: int, out_tok: int) -> dict[str, object]:
    ts = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    return {
        "_key": uuid.uuid4().hex,
        "tags": {"tenant_id": tenant_id, "session_id": "s", "agent_name": "a"},
        "timestamp_start": ts,
        "cost_usd": cost,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


@pytest_asyncio.fixture(scope="function")
async def quota_app(arango_container: ArangoEndpoint) -> AsyncIterator[FastAPI]:
    db_name = f"c8_quota_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    db.create_collection(COLL_CALLS)
    app = FastAPI()
    app.include_router(quota_router)
    app.state.quota_service = QuotaService(QuotaRepository(db))
    app.state.quota_db = db  # for the test to seed llm_calls
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://c8-admin",
    )


async def test_default_policy_then_round_trip(quota_app: FastAPI) -> None:
    async with _client(quota_app) as c:
        got = await c.get("/admin/v1/quota/policy", headers=_TMGR_HEADERS)
        assert got.status_code == 200
        assert {w["key"] for w in got.json()["windows"]} == {"session", "week", "month"}

        new_policy = {
            "windows": [
                {
                    "key": "session",
                    "label": "Session (5h)",
                    "duration_seconds": 18000,
                    "anchor": "calendar_month",
                    "warn_threshold_pct": 80.0,
                    "limits": {"tenant": {"max_cost_usd": 10.0, "max_tokens": None}},
                }
            ]
        }
        put = await c.put("/admin/v1/quota/policy", headers=_TMGR_HEADERS, json=new_policy)
        assert put.status_code == 200, put.text
        reread = await c.get("/admin/v1/quota/policy", headers=_TMGR_HEADERS)
    assert reread.json()["windows"][0]["limits"]["tenant"]["max_cost_usd"] == 10.0


async def test_status_reflects_real_usage_and_blocks(quota_app: FastAPI) -> None:
    db = quota_app.state.quota_db
    # Two calls for tenant t1 totalling $12 — over a $10 cap.
    db.collection(COLL_CALLS).insert(_call_doc("t1", 7.0, 1000, 500))
    db.collection(COLL_CALLS).insert(_call_doc("t1", 5.0, 800, 400))
    # A call for a DIFFERENT tenant must not count.
    db.collection(COLL_CALLS).insert(_call_doc("other", 99.0, 1, 1))

    policy = {
        "windows": [
            {
                "key": "session",
                "label": "Session (5h)",
                "duration_seconds": 18000,
                "anchor": "calendar_month",
                "warn_threshold_pct": 80.0,
                "limits": {"tenant": {"max_cost_usd": 10.0, "max_tokens": None}},
            }
        ]
    }
    async with _client(quota_app) as c:
        await c.put("/admin/v1/quota/policy", headers=_TMGR_HEADERS, json=policy)
        status = await c.get(
            "/admin/v1/quota/status?tenant_id=t1", headers=_TMGR_HEADERS
        )
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["blocked"] is True
    w = body["windows"][0]
    assert w["usage_cost_usd"] == 12.0
    assert w["usage_tokens"] == 2700
    assert w["state"] == "exceeded"


async def test_quota_policy_requires_tenant_manager(quota_app: FastAPI) -> None:
    async with _client(quota_app) as c:
        get = await c.get("/admin/v1/quota/policy", headers=_USER_HEADERS)
        put = await c.put("/admin/v1/quota/policy", headers=_USER_HEADERS, json={"windows": []})
        anon = await c.get("/admin/v1/quota/policy")
    assert get.status_code == 403
    assert put.status_code == 403
    assert anon.status_code == 401


async def test_self_quota_uses_caller_tenant_header(quota_app: FastAPI) -> None:
    db = quota_app.state.quota_db
    db.collection(COLL_CALLS).insert(_call_doc("t-self", 3.0, 100, 50))
    # A PLAIN user (no tenant_manager) reads their OWN tenant via X-Tenant-Id.
    headers = {"X-User-Id": "u-member", "X-User-Roles": "user", "X-Tenant-Id": "t-self"}
    async with _client(quota_app) as c:
        me = await c.get("/api/v1/quota/me", headers=headers)
        anon = await c.get("/api/v1/quota/me")
        no_tenant = await c.get("/api/v1/quota/me", headers={"X-User-Id": "u-member"})
    assert me.status_code == 200, me.text
    assert me.json()["tenant_id"] == "t-self"
    # The session (windows[0], first_use) shows 0 until the guard opens a session;
    # the calendar week window (windows[1]) sums the seeded call deterministically.
    assert me.json()["windows"][1]["usage_cost_usd"] == 3.0
    assert anon.status_code == 401  # no identity
    assert no_tenant.status_code == 400  # authenticated but no tenant header
