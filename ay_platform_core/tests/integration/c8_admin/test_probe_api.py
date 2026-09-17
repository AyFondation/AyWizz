# =============================================================================
# File: test_probe_api.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_admin/test_probe_api.py
# Description: Integration test for the provider / model probes against a real
#              ArangoDB testcontainer — router → service → repository → Arango.
#
#              WHY INTEGRATION AND NOT ONLY UNIT. The unit suite
#              (tests/unit/c8_llm/test_probe_service.py) covers URL
#              composition, auth shape and the capability verdicts with a fake
#              store. What it cannot cover is the part that actually broke
#              things historically: a provider document READ BACK FROM THE
#              DATABASE, whose `base_url` was stored by an earlier schema
#              version. The 2026-09-09 outage was exactly that — a value that
#              round-tripped through Arango carrying a trailing slash.
#
#              THE UPSTREAM IS DELIBERATELY UNREACHABLE. Standing up a fake
#              provider server would test the fake; pointing at an address that
#              cannot resolve produces a real, deterministic verdict AND
#              exercises the whole stack, including the composed URL the probe
#              reports back.
#
#              @relation validates:R-800-150
#              @relation validates:R-800-151
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.registry.probe_service import LLMProbeService
from ay_platform_core.c8_llm.registry.provider_repository import LLMProviderRepository
from ay_platform_core.c8_llm.registry.provider_router import router as provider_router
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.router import router as registry_router
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_PM_HEADERS = {"X-User-Id": "u-pm", "X-User-Roles": "platform_manager"}

# Reserved by RFC 6761 to never resolve. A deterministic "unreachable" that
# does not depend on the runner's network policy.
_DEAD_HOST = "https://probe.invalid"


@pytest_asyncio.fixture(scope="function")
async def probe_app(arango_container: ArangoEndpoint) -> AsyncIterator[FastAPI]:
    db_name = f"c8_probe_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)

    repo = LLMRegistryRepository(db)
    repo._ensure_collections_sync()
    provider_repo = LLMProviderRepository(db)
    provider_repo._ensure_collections_sync()
    cipher = SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")

    app = FastAPI()
    app.include_router(registry_router)
    app.include_router(provider_router)
    app.state.registry_service = LLMRegistryService(repo)
    app.state.provider_service = LLMProviderService(provider_repo, cipher)
    # No completer: the model probe must then report `not_configured` rather
    # than quietly calling the provider directly (R-800-151).
    app.state.probe_service = LLMProbeService(
        provider_repo, repo, cipher, None, timeout_seconds=3.0,
    )
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://c8-admin",
    )


async def _create_provider(client: httpx.AsyncClient, base_url: str) -> str:
    resp = await client.post(
        "/admin/v1/llm/providers",
        json={
            "name": f"probe-{uuid.uuid4().hex[:6]}",
            "base_url": base_url,
            "wire_format": "openai",
        },
        headers=_PM_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["provider_id"])


async def _create_model(client: httpx.AsyncClient, provider_id: str) -> str:
    resp = await client.post(
        "/admin/v1/llm/registry",
        json={
            "alias": f"probe-model-{uuid.uuid4().hex[:6]}",
            "provider_id": provider_id,
            "upstream_model": "gpt-4o-mini",
            "capabilities": {
                "vision": False, "tool_calling": True, "context_window": 128000,
            },
            "provider_cost_in_per_1m": 0.15,
            "provider_cost_out_per_1m": 0.60,
            "default_model_quality": "low",
            "enabled": True,
        },
        headers=_PM_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["model_id"])


async def test_provider_without_a_model_has_no_pipeline_path(
    probe_app: FastAPI,
) -> None:
    """The probe reaches a provider only through one of its models, because
    the pipeline's unit of work is a completion. With none configured there is
    nothing to exercise, and saying so beats inventing a verdict."""
    async with _client(probe_app) as client:
        provider_id = await _create_provider(client, _DEAD_HOST)
        resp = await client.post(
            f"/admin/v1/llm/providers/{provider_id}/probe", headers=_PM_HEADERS,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "not_configured"
    assert "no model is configured" in body["error"]


async def test_probe_reports_the_api_base_as_stored_after_a_round_trip(
    probe_app: FastAPI,
) -> None:
    """The operator types a trailing slash; the validator strips it on write;
    the probe reports what the PIPELINE will actually send as `api_base`.

    This is the 2026-09-09 shape — a `base_url` that round-trips through
    Arango — checked end to end rather than against an in-memory object."""
    async with _client(probe_app) as client:
        provider_id = await _create_provider(client, _DEAD_HOST + "/")
        await _create_model(client, provider_id)
        resp = await client.post(
            f"/admin/v1/llm/providers/{provider_id}/probe", headers=_PM_HEADERS,
        )

    body = resp.json()
    assert body["effective_url"] == _DEAD_HOST
    assert not body["effective_url"].endswith("/")
    # A model exists, so the probe got as far as needing a gateway client —
    # which this fixture deliberately does not wire (see `probe_app`).
    assert body["outcome"] == "not_configured"
    assert "gateway client" in body["error"]


async def test_unknown_provider_probe_is_not_configured(probe_app: FastAPI) -> None:
    async with _client(probe_app) as client:
        resp = await client.post(
            "/admin/v1/llm/providers/does-not-exist/probe", headers=_PM_HEADERS,
        )

    assert resp.status_code == 200
    assert resp.json()["outcome"] == "not_configured"


async def test_model_probe_refuses_rather_than_bypassing_the_production_path(
    probe_app: FastAPI,
) -> None:
    """With no gateway client wired, the probe reports `not_configured`. It
    must NOT fall back to calling the provider directly with the stored key:
    that would report health for a route it never exercised (R-800-151)."""
    async with _client(probe_app) as client:
        provider_id = await _create_provider(client, _DEAD_HOST)
        created = await client.post(
            "/admin/v1/llm/registry",
            json={
                "alias": f"probe-model-{uuid.uuid4().hex[:6]}",
                "provider_id": provider_id,
                "upstream_model": "gpt-4o-mini",
                "capabilities": {
                    "vision": False, "tool_calling": True, "context_window": 128000,
                },
                "provider_cost_in_per_1m": 0.15,
                "provider_cost_out_per_1m": 0.60,
                "default_model_quality": "low",
                "enabled": True,
            },
            headers=_PM_HEADERS,
        )
        assert created.status_code == 201, created.text
        model_id = created.json()["model_id"]

        resp = await client.post(
            f"/admin/v1/llm/registry/{model_id}/probe", headers=_PM_HEADERS,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "not_configured"
    # Even when it cannot call, it reports what it WOULD have sent — the
    # operator can spot a wrong upstream id without spending a token.
    assert body["resolved_target"] == "openai/gpt-4o-mini"


async def test_capabilities_persist_with_unknown_provenance_by_default(
    probe_app: FastAPI,
) -> None:
    """A model created through the HMI carries flags nobody measured. They read
    back as `unknown`, which is what they are — not as `False` (R-800-152)."""
    async with _client(probe_app) as client:
        provider_id = await _create_provider(client, _DEAD_HOST)
        created = await client.post(
            "/admin/v1/llm/registry",
            json={
                "alias": f"prov-default-{uuid.uuid4().hex[:6]}",
                "provider_id": provider_id,
                "upstream_model": "gpt-4o-mini",
                "capabilities": {
                    "vision": False, "tool_calling": True, "context_window": 128000,
                },
                "provider_cost_in_per_1m": 0.15,
                "provider_cost_out_per_1m": 0.60,
                "default_model_quality": "low",
                "enabled": True,
            },
            headers=_PM_HEADERS,
        )

    assert created.status_code == 201, created.text
    provenance = created.json()["capabilities"]["provenance"]
    assert provenance["tool_calling"] == "unknown"
    assert provenance["vision"] == "unknown"
