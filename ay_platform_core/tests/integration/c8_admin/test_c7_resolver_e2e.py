# =============================================================================
# File: test_c7_resolver_e2e.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_admin/test_c7_resolver_e2e.py
# Description: CROSS-COMPONENT end-to-end of the model-quality resolution path
#              against a REAL ArangoDB: the actual C7 `LLMResolverClient` driving
#              the actual c8_admin app over ASGI. v2: seeds via the provider +
#              model registries (provider → model by id → catalogue by id), then
#              C7 resolves `model_quality` → the model's current alias.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c7_memory.llm_resolver import LLMResolverClient
from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_router import router as catalog_router
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
from ay_platform_core.c8_llm.registry.provider_repository import LLMProviderRepository
from ay_platform_core.c8_llm.registry.provider_router import router as provider_router
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.router import router as registry_router
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-res"
_TMGR = {"X-User-Id": "u-tmgr", "X-User-Roles": "platform_manager"}
_ADMIN = {"X-User-Id": "u-adm", "X-User-Roles": "admin", "X-Tenant-Id": _TENANT}
_PROVIDER = {
    "name": "Anthropic",
    "base_url": "https://api.anthropic.com",
    "wire_format": "anthropic",
}


def _model_body(provider_id: str, alias: str) -> dict[str, object]:
    return {
        "alias": alias,
        "provider_id": provider_id,
        "upstream_model": "claude-haiku-4-5-20251001",
        "capabilities": {"vision": True, "tool_calling": True, "context_window": 200000},
        "provider_cost_in_per_1m": 0.8,
        "provider_cost_out_per_1m": 4.0,
        "default_model_quality": "low",
        "enabled": True,
    }


@pytest_asyncio.fixture(scope="function")
async def c8_admin_app(arango_container: ArangoEndpoint) -> AsyncIterator[FastAPI]:
    db_name = f"c8_res_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    repo = LLMRegistryRepository(db)
    repo._ensure_collections_sync()
    provider_repo = LLMProviderRepository(db)
    provider_repo._ensure_collections_sync()
    catalog_repo = TenantCatalogRepository(db)
    catalog_repo._ensure_collections_sync()
    registry_service = LLMRegistryService(repo)
    cipher = SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")
    app = FastAPI()
    app.include_router(registry_router)
    app.include_router(provider_router)
    app.include_router(catalog_router)
    app.state.registry_service = registry_service
    app.state.provider_service = LLMProviderService(provider_repo, cipher)
    app.state.catalog_service = TenantCatalogService(catalog_repo, registry_service)
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


def _admin_http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://c8-admin",
    )


def _c7_resolver(app: FastAPI) -> LLMResolverClient:
    return LLMResolverClient(
        base_url="http://c8-admin",
        client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://c8-admin"
        ),
    )


async def _seed(admin: httpx.AsyncClient, alias: str) -> str:
    prov = await admin.post("/admin/v1/llm/providers", headers=_TMGR, json=_PROVIDER)
    assert prov.status_code == 201, prov.text
    reg = await admin.post(
        "/admin/v1/llm/registry", headers=_TMGR, json=_model_body(prov.json()["provider_id"], alias)
    )
    assert reg.status_code == 201, reg.text
    model_id = str(reg.json()["model_id"])
    cat = await admin.put(
        f"/api/v1/llm/catalog/{model_id}", headers=_ADMIN, json={"enabled": True}
    )
    assert cat.status_code == 200, cat.text
    return model_id


async def test_c7_resolves_quality_through_real_c8_admin(c8_admin_app: FastAPI) -> None:
    alias = "claude-haiku-fast"
    async with _admin_http(c8_admin_app) as admin:
        await _seed(admin, alias)

    resolver = _c7_resolver(c8_admin_app)
    text_alias = await resolver.resolve(tenant_id=_TENANT, user_id="u-adm", model_quality="low")
    vision_alias = await resolver.resolve(
        tenant_id=_TENANT, user_id="u-adm", model_quality="low", require_vision=True
    )
    missing = await resolver.resolve(tenant_id=_TENANT, user_id="u-adm", model_quality="high")
    await resolver.aclose()

    assert text_alias == alias
    assert vision_alias == alias
    assert missing is None


async def test_c7_resolver_isolated_per_tenant_through_real_c8_admin(
    c8_admin_app: FastAPI,
) -> None:
    # 800 v10 opt-out: both tenants inherit the shared registry baseline, but one
    # tenant's CUSTOMISATION (disabling the model) does not leak to the other.
    async with _admin_http(c8_admin_app) as admin:
        mid = await _seed(admin, "claude-haiku-fast")
        disabled = await admin.put(
            f"/api/v1/llm/catalog/{mid}", headers=_ADMIN, json={"enabled": False}
        )
        assert disabled.status_code == 200, disabled.text

    resolver = _c7_resolver(c8_admin_app)
    mine = await resolver.resolve(tenant_id=_TENANT, user_id="u-adm", model_quality="low")
    other = await resolver.resolve(tenant_id="tenant-other", user_id="u-x", model_quality="low")
    await resolver.aclose()
    assert mine is None  # _TENANT disabled its only model
    assert other == "claude-haiku-fast"  # a fresh tenant keeps the baseline
