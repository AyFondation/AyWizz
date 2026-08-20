# =============================================================================
# File: test_cost_receiver_api.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_llm/test_cost_receiver_api.py
# Description: Integration test for the C8 cost receiver (R-800-070) against
#              a real ArangoDB testcontainer. POSTs a forwarder envelope to
#              `/internal/llm-calls` and asserts one `llm_calls` row is
#              persisted with the cost computed from the model catalog and
#              the tags projected from the forward-auth headers.
#
# @relation validates:R-800-070
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.cost_sink_arango import COLLECTION
from ay_platform_core.c8_llm.main import CostReceiverConfig, create_app
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
async def receiver_app(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[tuple[FastAPI, object]]:
    db_name = f"c8_recv_{uuid.uuid4().hex[:8]}"
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
    # httpx ASGITransport does NOT run the FastAPI lifespan, so ensure the
    # collection here (same pattern as the c4 documents_app fixture).
    app.state.cost_sink.ensure_collection()
    db = client.db(db_name, username="root", password=arango_container.password)
    try:
        yield app, db
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://c8-recv",
    )


async def test_ingest_persists_llm_call_with_cost_from_registry(
    receiver_app: tuple[FastAPI, object],
) -> None:
    """Bloc 5 (D-011): cost is computed from the in-app REGISTRY (operator-set
    per-model `provider_cost`), NOT the neutral config catalog. The C8 client
    resolves a tier to the model's `<wire>/<upstream>`, which the proxy reports
    as `model`; the receiver looks that up in a registry-derived catalog."""
    app, db = receiver_app
    # Seed one provider + one model (any provider family — provider-independent).
    db.create_collection("llm_providers")  # type: ignore[attr-defined]
    db.create_collection("llm_registry")  # type: ignore[attr-defined]
    db.collection("llm_providers").insert(  # type: ignore[attr-defined]
        {"_key": "p1", "name": "Anthropic", "wire_format": "anthropic"}
    )
    db.collection("llm_registry").insert(  # type: ignore[attr-defined]
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
    envelope = {
        "status": "success",
        "model": "anthropic/claude-haiku-4-5",  # the RESOLVED model the proxy sees
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cached_tokens": 0},
        "headers": {
            "X-Agent-Name": "c3-docgen",
            "X-Tenant-Id": "t1",
            "X-Project-Id": "p1",
            "X-Session-Id": "s1",
        },
        "fingerprint": "sha256:abc",
        "start_time": "2026-05-22T10:00:00+00:00",
        "end_time": "2026-05-22T10:00:02+00:00",
    }
    async with _client(app) as c:
        resp = await c.post("/internal/llm-calls", json=envelope)
        assert resp.status_code == 200, resp.text
        call_id = resp.json()["stored"]

    doc = db.collection(COLLECTION).get(call_id)  # type: ignore[attr-defined]
    assert doc is not None
    assert doc["model"] == "anthropic/claude-haiku-4-5"
    assert doc["provider"] == "anthropic"
    assert doc["input_tokens"] == 1000
    assert doc["output_tokens"] == 500
    assert doc["latency_ms"] == 2000
    assert doc["status"] == "success"
    # Cost from the REGISTRY: 1000 in @ 0.8/M + 500 out @ 4.0/M = 0.0028.
    assert doc["cost_usd"] == pytest.approx(0.0028)
    assert doc["tags"]["agent_name"] == "c3-docgen"
    assert doc["tags"]["tenant_id"] == "t1"
    assert doc["tags"]["project_id"] == "p1"


async def test_health_ok(receiver_app: tuple[FastAPI, object]) -> None:
    app, _ = receiver_app
    async with _client(app) as c:
        resp = await c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["component"] == "c8_cost_receiver"
