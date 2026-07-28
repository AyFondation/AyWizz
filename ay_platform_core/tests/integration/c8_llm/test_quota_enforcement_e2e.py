# =============================================================================
# File: test_quota_enforcement_e2e.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_llm/test_quota_enforcement_e2e.py
# Description: Exhaustive end-to-end quota ENFORCEMENT tests. Each test (1)
#              configures a real policy, (2) seeds the `llm_calls` ledger with a
#              CONTROLLED cost/token consumption, (3) drives a real guarded C8
#              LLMGatewayClient call whose LLM response is MOCKED (deterministic
#              via httpx.MockTransport), and (4) asserts the exact REACTION:
#              call proceeds (ok), proceeds + warns (soft), or is BLOCKED before
#              any upstream spend (hard → QuotaExceededError → 429 at the app).
#              Covers every level (global/tenant/project/user), both dimensions
#              (cost + tokens), warn vs block, the four-level clamping, the
#              first-use session anchor, calendar windows, and the status views.
#
# @relation validates:R-800-042
# =============================================================================

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import ChatCompletionRequest, ChatMessage, ChatRole
from ay_platform_core.c8_llm.quota.guard import QuotaExceededError, build_quota_guard
from ay_platform_core.c8_llm.quota.http import register_quota_handler
from ay_platform_core.c8_llm.quota.models import QuotaLimits, QuotaWindow
from ay_platform_core.c8_llm.quota.repository import COLL_CALLS, QuotaRepository
from ay_platform_core.c8_llm.quota.router import router as quota_router
from ay_platform_core.c8_llm.quota.service import QuotaService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# --- Fixture: a fresh Arango DB with the quota collections -------------------


@pytest_asyncio.fixture(scope="function")
async def quota_db(arango_container: ArangoEndpoint) -> AsyncIterator[Any]:
    db_name = f"c8_quota_e2e_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    db.create_collection(COLL_CALLS)
    try:
        yield db
    finally:
        cleanup_arango_database(arango_container, db_name)


# --- Controlled inputs: seed usage, configure policy, mock the LLM ----------


def _seed_call(
    db: Any,
    *,
    cost: float,
    in_tok: int = 0,
    out_tok: int = 0,
    tenant: str = "t1",
    project: str | None = None,
    user: str | None = None,
    minutes_ago: float = 1.0,
) -> None:
    """Insert a CONTROLLED llm_calls row — this is the deterministic 'measured
    consumption' the quota reads (the real cost-tracker path is exercised
    separately by test_cost_receiver_api)."""
    tags: dict[str, str] = {"tenant_id": tenant, "session_id": "s", "agent_name": "a"}
    if project is not None:
        tags["project_id"] = project
    if user is not None:
        tags["user_id"] = user
    db.collection(COLL_CALLS).insert(
        {
            "_key": uuid.uuid4().hex,
            "tags": tags,
            "timestamp_start": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(),
            "cost_usd": cost,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }
    )


async def _set_policy(db: Any, *windows: QuotaWindow) -> None:
    await QuotaService(QuotaRepository(db)).set_policy(list(windows))


def _week(**limits: QuotaLimits) -> QuotaWindow:
    """A calendar-week window (deterministic: usage summed since Monday) with the
    given per-level limits — avoids first-use anchor timing in level tests."""
    return QuotaWindow(
        key="week",
        label="Week",
        duration_seconds=7 * 86400,
        anchor="calendar_week",
        warn_threshold_pct=80.0,
        limits={k: v for k, v in limits.items()},  # type: ignore[misc]
    )


def _mock_llm_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
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


def _client(db: Any) -> LLMGatewayClient:
    transport = httpx.MockTransport(_mock_llm_response)
    http = httpx.AsyncClient(transport=transport, base_url="http://c8:8000/v1")
    return LLMGatewayClient(
        ClientSettings(gateway_url="http://c8:8000/v1"),
        bearer_token="test-token",
        http_client=http,
        quota_guard=build_quota_guard(db),
    )


def _payload() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="claude-haiku-fast",
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
    )


async def _call(
    db: Any, *, tenant: str = "t1", project: str | None = None, user: str | None = None
) -> Any:
    client = _client(db)
    try:
        return await client.chat_completion(
            _payload(),
            agent_name="c3-rag",
            session_id="s1",
            tenant_id=tenant,
            project_id=project,
            user_id=user,
        )
    finally:
        await client.aclose()


# --- 1. Inert by default -----------------------------------------------------


async def test_no_policy_never_blocks(quota_db: Any) -> None:
    _seed_call(quota_db, cost=999.0, tenant="t1")  # huge spend, but no limits
    resp = await _call(quota_db, tenant="t1", user="u1")
    assert resp.choices[0].message.content == "ok"


# --- 2. Tenant level: ok / warn / block (cost) -------------------------------


async def test_tenant_cost_under_cap_proceeds(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=10.0)))
    _seed_call(quota_db, cost=4.0, tenant="t1")
    resp = await _call(quota_db, tenant="t1")
    assert resp.choices[0].message.content == "ok"


async def test_tenant_cost_at_warn_threshold_proceeds_and_warns(
    quota_db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=10.0)))
    _seed_call(quota_db, cost=8.5, tenant="t1")  # 85% ≥ 80% warn threshold
    with caplog.at_level(logging.WARNING):
        resp = await _call(quota_db, tenant="t1")
    assert resp.choices[0].message.content == "ok"
    assert any("approaching" in r.message for r in caplog.records)


async def test_tenant_cost_over_cap_is_blocked_before_spend(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=10.0)))
    _seed_call(quota_db, cost=12.0, tenant="t1")  # over the $10 cap
    with pytest.raises(QuotaExceededError) as exc:
        await _call(quota_db, tenant="t1")
    assert exc.value.status.blocked is True


# --- 3. Per-user level blocks even when the tenant is fine -------------------


async def test_user_cap_blocks_while_tenant_ok(quota_db: Any) -> None:
    await _set_policy(
        quota_db,
        _week(
            tenant=QuotaLimits(max_cost_usd=1000.0),
            user=QuotaLimits(max_cost_usd=5.0),
        ),
    )
    _seed_call(quota_db, cost=6.0, tenant="t1", user="u1")  # user over $5; tenant fine
    with pytest.raises(QuotaExceededError):
        await _call(quota_db, tenant="t1", user="u1")
    # A DIFFERENT user under their own cap is NOT blocked.
    resp = await _call(quota_db, tenant="t1", user="u2")
    assert resp.choices[0].message.content == "ok"


# --- 4. Per-project level ----------------------------------------------------


async def test_project_cap_blocks(quota_db: Any) -> None:
    await _set_policy(
        quota_db,
        _week(
            tenant=QuotaLimits(max_cost_usd=1000.0),
            project=QuotaLimits(max_cost_usd=5.0),
        ),
    )
    _seed_call(quota_db, cost=6.0, tenant="t1", project="p1")
    with pytest.raises(QuotaExceededError):
        await _call(quota_db, tenant="t1", project="p1")


# --- 5. Global platform aggregate (across tenants) --------------------------


async def test_global_aggregate_blocks_across_tenants(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(**{"global": QuotaLimits(max_cost_usd=10.0)}))
    _seed_call(quota_db, cost=7.0, tenant="t1")
    _seed_call(quota_db, cost=5.0, tenant="t2")  # platform total $12 > $10
    with pytest.raises(QuotaExceededError):
        await _call(quota_db, tenant="t3")  # a third tenant is blocked by the global cap


# --- 6. Token dimension ------------------------------------------------------


async def test_token_dimension_blocks(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_tokens=1000)))
    _seed_call(quota_db, cost=0.0, in_tok=800, out_tok=300, tenant="t1")  # 1100 > 1000
    with pytest.raises(QuotaExceededError):
        await _call(quota_db, tenant="t1")


# --- 7. First-use session anchor: a fixed block from the first call ----------


async def test_first_use_session_enforces_within_the_fixed_block(quota_db: Any) -> None:
    session = QuotaWindow(
        key="session",
        label="Session (5h)",
        duration_seconds=5 * 3600,
        anchor="first_use",
        limits={"user": QuotaLimits(max_cost_usd=10.0)},
    )
    await _set_policy(quota_db, session)
    # Pre-open the session 1h ago + seed usage inside the block → over the cap.
    repo = QuotaRepository(quota_db)
    await repo.set_anchor(
        "session:user:u1", (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    )
    _seed_call(quota_db, cost=12.0, tenant="t1", user="u1", minutes_ago=30)
    with pytest.raises(QuotaExceededError):
        await _call(quota_db, tenant="t1", user="u1")
    # A user with NO open session is not blocked (the guard opens a fresh one).
    resp = await _call(quota_db, tenant="t1", user="u-fresh")
    assert resp.choices[0].message.content == "ok"


# --- 8. The guard OPENS a session on the first call (advance) ----------------


async def test_guard_opens_session_anchor_on_first_call(quota_db: Any) -> None:
    session = QuotaWindow(
        key="session",
        label="Session",
        duration_seconds=5 * 3600,
        anchor="first_use",
        limits={"user": QuotaLimits(max_cost_usd=10.0)},
    )
    await _set_policy(quota_db, session)
    await _call(quota_db, tenant="t1", user="u-new")  # first call opens the session
    anchor = await QuotaRepository(quota_db).get_anchor("session:user:u-new")
    assert anchor is not None  # the guard wrote the anchor


# --- 9. The HTTP REACTION: QuotaExceededError → 429 at the app boundary ------


async def test_blocked_call_surfaces_as_http_429(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=10.0)))
    _seed_call(quota_db, cost=12.0, tenant="t1")

    app = FastAPI()
    register_quota_handler(app)

    @app.post("/call")
    async def _route() -> dict[str, str]:
        await _call(quota_db, tenant="t1")
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as c:
        resp = await c.post("/call")
    assert resp.status_code == 429
    assert resp.json()["quota"]["blocked"] is True


# --- 10. The status endpoints reflect per-level state -----------------------


async def test_status_endpoints_report_per_level(quota_db: Any) -> None:
    await _set_policy(
        quota_db,
        _week(
            tenant=QuotaLimits(max_cost_usd=10.0),
            user=QuotaLimits(max_cost_usd=10.0),
        ),
    )
    _seed_call(quota_db, cost=12.0, tenant="t1", user="u1")

    app = FastAPI()
    app.include_router(quota_router)
    app.state.quota_service = QuotaService(QuotaRepository(quota_db))
    tmgr = {"X-User-Id": "op", "X-User-Roles": "platform_manager"}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://c8") as c:
        admin = await c.get("/admin/v1/quota/status?tenant_id=t1", headers=tmgr)
        me = await c.get(
            "/api/v1/quota/me", headers={"X-User-Id": "u1", "X-Tenant-Id": "t1"}
        )
    assert admin.status_code == 200 and admin.json()["blocked"] is True
    # /quota/me exposes the caller's own user level.
    levels = {lv["level"] for lv in me.json()["windows"][0]["levels"]}
    assert {"global", "tenant", "user"} <= levels
    assert me.json()["blocked"] is True


# --- 11. Config-time clamping: a child cap may not exceed its parent ---------


async def test_policy_rejects_user_cap_above_tenant() -> None:
    with pytest.raises(ValueError, match="may not permit more"):
        _week(
            tenant=QuotaLimits(max_cost_usd=10.0),
            user=QuotaLimits(max_cost_usd=20.0),  # > tenant
        )


# --- 11b. The eager check (used by streaming endpoints) blocks without a call -


async def test_check_quota_raises_without_sending_a_call(quota_db: Any) -> None:
    # `check_quota` is what a STREAMING caller (C3 chat) runs BEFORE returning its
    # SSE response, so a hard block becomes a 429 rather than a broken stream.
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=10.0)))
    _seed_call(quota_db, cost=12.0, tenant="t1")
    client = _client(quota_db)
    try:
        with pytest.raises(QuotaExceededError):
            await client.check_quota(tenant_id="t1", user_id="u1")
    finally:
        await client.aclose()


# --- 12. A passing call still works end to end (mock LLM returns content) ----


async def test_unblocked_call_returns_mocked_content(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=100.0)))
    _seed_call(quota_db, cost=1.0, tenant="t1")
    resp = await _call(quota_db, tenant="t1", user="u1")
    assert resp.choices[0].message.content == "ok"
    assert resp.usage.total_tokens == 2


# --- 13. Exhaustive matrix: level x dimension x state -----------------------

_CAP_COST = 10.0
_CAP_TOK = 1000


def _subject_for(level: str) -> tuple[str, str | None, str | None]:
    """(tenant, project, user) ids putting usage at the requested level."""
    return "t1", ("p1" if level == "project" else None), ("u1" if level == "user" else None)


@pytest.mark.parametrize("level", ["global", "tenant", "project", "user"])
@pytest.mark.parametrize("dim", ["cost", "tokens"])
@pytest.mark.parametrize(
    ("state", "factor", "expect_block"),
    [("ok", 0.4, False), ("warn", 0.9, False), ("block", 1.1, True)],
)
async def test_level_dimension_state_matrix(
    quota_db: Any, level: str, dim: str, state: str, factor: float, expect_block: bool
) -> None:
    cap = _CAP_COST if dim == "cost" else float(_CAP_TOK)
    limits = (
        QuotaLimits(max_cost_usd=cap) if dim == "cost" else QuotaLimits(max_tokens=int(cap))
    )
    await _set_policy(quota_db, _week(**{level: limits}))
    tenant, project, user = _subject_for(level)
    amount = cap * factor
    if dim == "cost":
        _seed_call(quota_db, cost=amount, tenant=tenant, project=project, user=user)
    else:
        _seed_call(
            quota_db, cost=0.0, in_tok=int(amount), tenant=tenant, project=project, user=user
        )
    if expect_block:
        with pytest.raises(QuotaExceededError):
            await _call(quota_db, tenant=tenant, project=project, user=user)
    else:
        resp = await _call(quota_db, tenant=tenant, project=project, user=user)
        assert resp.choices[0].message.content == "ok"


# --- 14. Boundary: usage == cap blocks (the limit is inclusive, `>=`) -------


@pytest.mark.parametrize(
    ("usage", "expect_block"), [(999, False), (1000, True), (1001, True)]
)
async def test_token_cap_boundary_is_inclusive(
    quota_db: Any, usage: int, expect_block: bool
) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_tokens=1000)))
    _seed_call(quota_db, cost=0.0, in_tok=usage, out_tok=0, tenant="t1")
    if expect_block:
        with pytest.raises(QuotaExceededError):
            await _call(quota_db, tenant="t1")
    else:
        resp = await _call(quota_db, tenant="t1")
        assert resp.choices[0].message.content == "ok"


# --- 15. Warn precision: fires AT the threshold, not below ------------------


@pytest.mark.parametrize(
    ("pct", "expect_warn"), [(0.79, False), (0.80, True), (0.95, True)]
)
async def test_warn_threshold_precision(
    quota_db: Any, caplog: pytest.LogCaptureFixture, pct: float, expect_warn: bool
) -> None:
    await _set_policy(
        quota_db, _week(tenant=QuotaLimits(max_cost_usd=100.0))
    )  # warn at 80%
    _seed_call(quota_db, cost=100.0 * pct, tenant="t1")
    with caplog.at_level(logging.WARNING):
        await _call(quota_db, tenant="t1")
    warned = any("approaching" in r.message for r in caplog.records)
    assert warned is expect_warn


# --- 15b. Cross-tenant isolation: /quota/me never leaks another tenant ------


async def test_quota_me_is_isolated_per_tenant(quota_db: Any) -> None:
    await _set_policy(quota_db, _week(tenant=QuotaLimits(max_cost_usd=100.0)))
    _seed_call(quota_db, cost=10.0, tenant="t1")
    _seed_call(quota_db, cost=50.0, tenant="t2")
    app = FastAPI()
    app.include_router(quota_router)
    app.state.quota_service = QuotaService(QuotaRepository(quota_db))

    def _tenant_cost(body: dict[str, Any]) -> float:
        lv = next(x for x in body["windows"][0]["levels"] if x["level"] == "tenant")
        return float(lv["usage_cost_usd"])

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://c8") as c:
        a = await c.get("/api/v1/quota/me", headers={"X-User-Id": "u", "X-Tenant-Id": "t1"})
        b = await c.get("/api/v1/quota/me", headers={"X-User-Id": "u", "X-Tenant-Id": "t2"})
        # A query param cannot override the forward-auth tenant (no peeking).
        spoof = await c.get(
            "/api/v1/quota/me?tenant_id=t2",
            headers={"X-User-Id": "u", "X-Tenant-Id": "t1"},
        )
    assert a.json()["tenant_id"] == "t1" and _tenant_cost(a.json()) == 10.0
    assert b.json()["tenant_id"] == "t2" and _tenant_cost(b.json()) == 50.0
    assert spoof.json()["tenant_id"] == "t1"  # the ?tenant_id=t2 is ignored


# --- 16. Multi-window: the most-constraining window blocks ------------------


async def test_multi_window_most_constraining_blocks(quota_db: Any) -> None:
    week = _week(tenant=QuotaLimits(max_cost_usd=10.0))
    month = QuotaWindow(
        key="month",
        label="Month",
        duration_seconds=30 * 86400,
        anchor="calendar_month",
        limits={"tenant": QuotaLimits(max_cost_usd=1000.0)},
    )
    await _set_policy(quota_db, week, month)
    _seed_call(quota_db, cost=12.0, tenant="t1")  # over week ($10), under month ($1000)
    with pytest.raises(QuotaExceededError):
        await _call(quota_db, tenant="t1")
