# =============================================================================
# File: test_quota_attribution_pipeline_e2e.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_llm/test_quota_attribution_pipeline_e2e.py
# Description: End-to-end attribution-pipeline test (gap #1). The other quota
#              tests SEED `llm_calls` directly; this one drives the REAL cost
#              path — a forwarder envelope (what the LiteLLM proxy posts after a
#              call; NO real LLM is invoked) → the C8 cost receiver → the
#              `llm_calls` ledger — and then asserts the QUOTA, reading that same
#              ledger, attributes the usage to the right user/project/tenant and
#              accumulates across calls. This locks the tag-key contract between
#              the cost tracker and the quota filter (e.g. `tags.user_id`) which
#              the seed-based tests cannot catch.
#
# @relation validates:R-800-042
# @relation validates:R-800-070
# =============================================================================

from __future__ import annotations

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

from ay_platform_core.c8_llm.cost_sink_arango import COLLECTION
from ay_platform_core.c8_llm.main import CostReceiverConfig, create_app
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
async def pipeline(arango_container: ArangoEndpoint) -> AsyncIterator[tuple[FastAPI, Any]]:
    db_name = f"c8_attr_{uuid.uuid4().hex[:8]}"
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
    # Bloc 5 (D-011): cost comes from the REGISTRY, not the neutral config
    # catalog. Seed one provider + model so the resolved model carries a cost.
    db.create_collection("llm_providers")
    db.create_collection("llm_registry")
    db.collection("llm_providers").insert(
        {"_key": "p1", "name": "Anthropic", "wire_format": "anthropic"}
    )
    db.collection("llm_registry").insert(
        {
            "_key": "m1",
            "provider_id": "p1",
            "alias": "fast",
            "upstream_model": "claude-haiku-4-5",
            "capabilities": {"context_window": 200000, "vision": False, "tool_calling": True},
            "provider_cost_in_per_1m": 0.8,
            "provider_cost_out_per_1m": 4.0,
            "enabled": True,
        }
    )
    try:
        yield app, db
    finally:
        cleanup_arango_database(arango_container, db_name)


def _envelope(
    *,
    in_tok: int,
    out_tok: int,
    tenant: str = "t1",
    project: str | None = None,
    user: str | None = None,
) -> dict[str, Any]:
    """A forwarder envelope as the proxy posts it — the CONTROLLED input that
    stands in for a real LLM call's cost event (no network/LLM involved)."""
    headers: dict[str, str] = {
        "X-Agent-Name": "c3-rag",
        "X-Session-Id": "s1",
        "X-Tenant-Id": tenant,
    }
    if project is not None:
        headers["X-Project-Id"] = project
    if user is not None:
        headers["X-User-Id"] = user
    # Recent timestamps so the row falls inside the current calendar window.
    now = datetime.now(UTC)
    return {
        "status": "success",
        "model": "anthropic/claude-haiku-4-5",  # RESOLVED model (registry cost)
        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok, "cached_tokens": 0},
        "headers": headers,
        "fingerprint": "sha256:abc",
        "start_time": (now - timedelta(seconds=2)).isoformat(),
        "end_time": now.isoformat(),
    }


async def _post(app: FastAPI, envelope: dict[str, Any]) -> None:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://c8-recv") as c:
        resp = await c.post("/internal/llm-calls", json=envelope)
        assert resp.status_code == 200, resp.text


async def _evaluate(db: Any, **kw: Any) -> Any:
    return await QuotaService(QuotaRepository(db)).evaluate(**kw)


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


# --- The receiver writes the SAME ledger the quota reads --------------------


async def test_receiver_ledger_collection_matches_quota_collection() -> None:
    # If these ever diverge, the quota silently reads an empty ledger.
    assert COLLECTION == COLL_CALLS


# --- The per-USER tag flows header → ledger → quota -------------------------


async def test_user_attribution_flows_end_to_end(pipeline: tuple[FastAPI, Any]) -> None:
    app, db = pipeline
    await _set_policy(db, _week())  # a calendar window so usage is summed deterministically
    await _post(app, _envelope(in_tok=800, out_tok=300, tenant="t1", user="u1"))
    # 1. The ledger row carries the user tag projected from X-User-Id.
    rows = list(db.collection(COLLECTION).all())
    assert len(rows) == 1
    assert rows[0]["tags"]["user_id"] == "u1"
    assert rows[0]["tags"]["tenant_id"] == "t1"
    # 2. The quota, reading that ledger, counts the 1100 tokens at the user level.
    status = await _evaluate(db, tenant_id="t1", user_id="u1")
    user_level = next(s for s in status.windows[0].levels if s.level == "user")
    assert user_level.usage_tokens == 1100


async def test_user_token_cap_blocks_via_real_pipeline(pipeline: tuple[FastAPI, Any]) -> None:
    app, db = pipeline
    await _set_policy(db, _week(user=QuotaLimits(max_tokens=1000)))
    await _post(app, _envelope(in_tok=800, out_tok=300, tenant="t1", user="u1"))  # 1100 > 1000
    status = await _evaluate(db, tenant_id="t1", user_id="u1")
    assert status.blocked is True
    # A DIFFERENT user, no usage, is not blocked.
    other = await _evaluate(db, tenant_id="t1", user_id="u2")
    assert other.blocked is False


# --- Accumulation across real calls -----------------------------------------


async def test_usage_accumulates_across_calls(pipeline: tuple[FastAPI, Any]) -> None:
    app, db = pipeline
    await _set_policy(db, _week(user=QuotaLimits(max_tokens=2500)))
    env = _envelope(in_tok=800, out_tok=300, tenant="t1", user="u1")  # 1100 each
    await _post(app, env)
    after_one = await _evaluate(db, tenant_id="t1", user_id="u1")
    assert after_one.blocked is False  # 1100 < 2500
    await _post(app, env)
    await _post(app, env)  # 3300 > 2500
    after_three = await _evaluate(db, tenant_id="t1", user_id="u1")
    assert after_three.blocked is True


# --- Cost (not just tokens) is computed from the REGISTRY + attributed ------


async def test_cost_from_registry_attributed_to_project(pipeline: tuple[FastAPI, Any]) -> None:
    app, db = pipeline
    # 1000 in @ $0.8/M + 500 out @ $4.0/M = $0.0028 per call (registry cost).
    await _set_policy(db, _week(project=QuotaLimits(max_cost_usd=0.005)))
    await _post(app, _envelope(in_tok=1000, out_tok=500, tenant="t1", project="p1"))
    one = await _evaluate(db, tenant_id="t1", project_id="p1")
    assert one.blocked is False  # $0.0028 < $0.005
    await _post(app, _envelope(in_tok=1000, out_tok=500, tenant="t1", project="p1"))
    two = await _evaluate(db, tenant_id="t1", project_id="p1")
    assert two.blocked is True  # $0.0056 > $0.005
