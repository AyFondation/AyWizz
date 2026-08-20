# =============================================================================
# File: test_app_factory.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_admin/test_app_factory.py
# Description: Exercises the REAL c8_admin `create_app` factory + its lifespan
#              against a real ArangoDB — the production wiring path (collections
#              ensured, registry SEEDED from the canonical litellm config, the
#              cipher built from the env master key, the middleware stack
#              mounted). Also covers the no-master-key boot (read paths live,
#              key WRITES → 503). This is the path a deployed pod runs, which
#              the inline-app integration tests do not cover.
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

from ay_platform_core.c8_admin.config import C8AdminConfig
from ay_platform_core.c8_admin.main import create_app
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_CANONICAL_CONFIG = (
    Path(__file__).resolve().parents[4]
    / "infra"
    / "c8_gateway"
    / "config"
    / "litellm-config.yaml"
)
_TMGR = {"X-User-Id": "u-tmgr", "X-User-Roles": "platform_manager"}


@pytest_asyncio.fixture(scope="function")
async def seeded_app(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[FastAPI]:
    db_name = f"c8_factory_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    cfg = C8AdminConfig(
        arango_url=arango_container.url,
        arango_db=db_name,
        arango_username="root",
        arango_password=arango_container.password,
        litellm_config_path=str(_CANONICAL_CONFIG),
        seed_on_start=True,
    )
    # Real factory: cipher from env (conftest sets AY_SECRET_MASTER_KEY).
    app = create_app(cfg)
    # Run the real lifespan (ensure collections + seed from the litellm config).
    async with app.router.lifespan_context(app):
        try:
            yield app
        finally:
            cleanup_arango_database(arango_container, db_name)


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://c8-admin",
    )


async def test_lifespan_starts_with_empty_registry_option_b(
    seeded_app: FastAPI,
) -> None:
    """Provider-independence / Option B (D-011): the canonical config is now
    entirely pass-through (neutral tiers + `*`), so the startup seed creates
    NOTHING — the registry is EMPTY until an operator registers a provider +
    model via the HMI. The factory + lifespan still boot cleanly."""
    async with _http(seeded_app) as c:
        health = await c.get("/health")
        listing = await c.get("/admin/v1/llm/registry", headers=_TMGR)
    assert health.status_code == 200
    assert health.json()["component"] == "c8_admin"
    assert listing.status_code == 200
    # No Anthropic (or any) model was seeded — the operator populates the
    # registry via the HMI.
    assert listing.json()["models"] == []


async def test_auth_guard_blocks_anonymous(seeded_app: FastAPI) -> None:
    async with _http(seeded_app) as c:
        resp = await c.get("/admin/v1/llm/registry")  # no forward-auth identity
    assert resp.status_code == 401


async def test_boots_without_master_key_and_key_write_is_503(
    arango_container: ArangoEndpoint,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AY_SECRET_MASTER_KEY", raising=False)
    monkeypatch.delenv("AY_SECRET_MASTER_KEYS", raising=False)
    db_name = f"c8_nokey_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    cfg = C8AdminConfig(
        arango_url=arango_container.url,
        arango_db=db_name,
        arango_username="root",
        arango_password=arango_container.password,
        seed_on_start=False,
    )
    # The factory builds WITHOUT a master key (cipher None) and still boots.
    app = create_app(cfg)
    try:
        async with app.router.lifespan_context(app), _http(app) as c:
            # Provider metadata write works (no key needed).
            prov = await c.post(
                "/admin/v1/llm/providers",
                headers=_TMGR,
                json={
                    "name": "Anthropic",
                    "base_url": "https://api.anthropic.com",
                    "wire_format": "anthropic",
                },
            )
            assert prov.status_code == 201, prov.text
            pid = prov.json()["provider_id"]
            # Key WRITE without a master key → 503 (key management unavailable).
            keyed = await c.put(
                f"/admin/v1/llm/providers/{pid}/api-key",
                headers=_TMGR,
                json={"api_key": "sk-ant-x"},
            )
            assert keyed.status_code == 503, keyed.text
    finally:
        cleanup_arango_database(arango_container, db_name)
