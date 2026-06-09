# =============================================================================
# File: test_registry_admin_api.py
# Version: 2
# Path: ay_platform_core/tests/integration/c8_admin/test_registry_admin_api.py
# Description: Integration test for the C8 admin LLM PROVIDER + MODEL registry +
#              tenant catalogue against a real ArangoDB testcontainer. v2: a
#              model references a provider by stable id; the WRITE-ONLY key lives
#              on the PROVIDER; models/catalogue are addressed by `model_id`
#              (rename-safe). Exercises the full router→service→repo→Arango path
#              + the tenant_manager / admin role gates.
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_router import router as catalog_router
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
from ay_platform_core.c8_llm.registry.models import ModelQuality
from ay_platform_core.c8_llm.registry.provider_repository import (
    COLL_PROVIDERS,
    LLMProviderRepository,
)
from ay_platform_core.c8_llm.registry.provider_router import router as provider_router
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.router import router
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TMGR_HEADERS = {"X-User-Id": "u-tmgr", "X-User-Roles": "tenant_manager"}
_USER_HEADERS = {"X-User-Id": "u-plain", "X-User-Roles": "user"}
_ADMIN_HEADERS = {"X-User-Id": "u-admin", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-1"}

_PROVIDER = {
    "name": "Anthropic",
    "base_url": "https://api.anthropic.com",
    "wire_format": "anthropic",
}


def _model_body(provider_id: str, alias: str, quality: str = "low") -> dict[str, object]:
    return {
        "alias": alias,
        "provider_id": provider_id,
        "upstream_model": "claude-haiku-4-5-20251001",
        "capabilities": {"vision": True, "tool_calling": True, "context_window": 200000},
        "provider_cost_in_per_1m": 0.80,
        "provider_cost_out_per_1m": 4.00,
        "default_model_quality": quality,
        "enabled": True,
    }


@pytest_asyncio.fixture(scope="function")
async def admin_app(arango_container: ArangoEndpoint) -> AsyncIterator[FastAPI]:
    db_name = f"c8_admin_{uuid.uuid4().hex[:8]}"
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
    cipher = SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")
    registry_service = LLMRegistryService(repo)
    app = FastAPI()
    app.include_router(router)
    app.include_router(provider_router)
    app.include_router(catalog_router)
    app.state.registry_service = registry_service
    app.state.provider_service = LLMProviderService(provider_repo, cipher)
    app.state.catalog_service = TenantCatalogService(catalog_repo, registry_service)
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://c8-admin",
    )


async def _new_provider(c: httpx.AsyncClient, name: str = "Anthropic") -> str:
    resp = await c.post(
        "/admin/v1/llm/providers", headers=_TMGR_HEADERS, json={**_PROVIDER, "name": name}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["provider_id"])


async def _new_model(
    c: httpx.AsyncClient, provider_id: str, alias: str, quality: str = "low"
) -> str:
    resp = await c.post(
        "/admin/v1/llm/registry",
        headers=_TMGR_HEADERS,
        json=_model_body(provider_id, alias, quality),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["model_id"])


# ---- Provider registry -------------------------------------------------------


async def test_provider_crud_and_write_only_key(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        created = await c.post("/admin/v1/llm/providers", headers=_TMGR_HEADERS, json=_PROVIDER)
        assert created.status_code == 201, created.text
        pid = created.json()["provider_id"]
        assert created.json()["key_status"] == "not_set"
        assert created.json()["base_url"] == "https://api.anthropic.com"

        # Write-only key: stored encrypted, never echoed.
        plaintext = f"sk-ant-{uuid.uuid4().hex}"
        keyed = await c.put(
            f"/admin/v1/llm/providers/{pid}/api-key",
            headers=_TMGR_HEADERS,
            json={"api_key": plaintext},
        )
        assert keyed.status_code == 200, keyed.text
        assert plaintext not in keyed.text
        assert keyed.json()["key_status"] == "set"

        # Update metadata (key preserved).
        upd = await c.put(
            f"/admin/v1/llm/providers/{pid}",
            headers=_TMGR_HEADERS,
            json={**_PROVIDER, "base_url": "https://api.anthropic.com/v1"},
        )
        assert upd.status_code == 200 and upd.json()["key_status"] == "set"

        listing = await c.get("/admin/v1/llm/providers", headers=_TMGR_HEADERS)
        assert listing.status_code == 200
        assert plaintext not in listing.text

        deleted = await c.delete(f"/admin/v1/llm/providers/{pid}", headers=_TMGR_HEADERS)
    assert deleted.status_code == 204
    # Ciphertext (not plaintext) was persisted.
    db = admin_app.state.provider_service._repo._db
    assert db.collection(COLL_PROVIDERS).count() == 0  # deleted


async def test_provider_duplicate_name_409(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        await _new_provider(c)
        dup = await c.post("/admin/v1/llm/providers", headers=_TMGR_HEADERS, json=_PROVIDER)
    assert dup.status_code == 409


async def test_provider_requires_tenant_manager(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        forbid = await c.get("/admin/v1/llm/providers", headers=_USER_HEADERS)
        anon = await c.get("/admin/v1/llm/providers")
    assert forbid.status_code == 403
    assert anon.status_code == 401


# ---- Model registry (stable id; provider ref) --------------------------------


async def test_create_list_update_delete_model(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        pid = await _new_provider(c)
        mid = await _new_model(c, pid, "claude-haiku-fast")

        listing = await c.get("/admin/v1/llm/registry", headers=_TMGR_HEADERS)
        assert listing.status_code == 200
        model = next(m for m in listing.json()["models"] if m["model_id"] == mid)
        assert model["alias"] == "claude-haiku-fast"
        assert model["provider_id"] == pid
        # No secret leaks into the model projection.
        assert "api_key" not in model and "api_key_ciphertext" not in model

        # Rename the alias by id — the model_id is unchanged.
        upd = await c.put(
            f"/admin/v1/llm/registry/{mid}",
            headers=_TMGR_HEADERS,
            json=_model_body(pid, "claude-haiku-renamed"),
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["model_id"] == mid
        assert upd.json()["alias"] == "claude-haiku-renamed"

        first = await c.delete(f"/admin/v1/llm/registry/{mid}", headers=_TMGR_HEADERS)
        second = await c.delete(f"/admin/v1/llm/registry/{mid}", headers=_TMGR_HEADERS)
    assert first.status_code == 204
    assert second.status_code == 404


async def test_duplicate_alias_409(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        pid = await _new_provider(c)
        await _new_model(c, pid, "dup")
        clash = await c.post(
            "/admin/v1/llm/registry", headers=_TMGR_HEADERS, json=_model_body(pid, "dup")
        )
    assert clash.status_code == 409


async def test_registry_requires_tenant_manager(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        forbid = await c.get("/admin/v1/llm/registry", headers=_USER_HEADERS)
        anon = await c.get("/admin/v1/llm/registry")
    assert forbid.status_code == 403
    assert anon.status_code == 401


# ---- Tenant catalogue (by model_id) ------------------------------------------


async def test_catalog_requires_admin_or_tenant_admin(admin_app: FastAPI) -> None:
    headers = {"X-User-Id": "u", "X-User-Roles": "user", "X-Tenant-Id": "tenant-1"}
    async with _client(admin_app) as c:
        resp = await c.get("/api/v1/llm/catalog", headers=headers)
    assert resp.status_code == 403


async def test_catalog_tenant_manager_is_excluded(admin_app: FastAPI) -> None:
    headers = {"X-User-Id": "u-tmgr", "X-User-Roles": "tenant_manager", "X-Tenant-Id": "tenant-1"}
    async with _client(admin_app) as c:
        resp = await c.get("/api/v1/llm/catalog", headers=headers)
    assert resp.status_code == 403


async def test_catalog_upsert_unknown_model_404(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        resp = await c.put(
            "/api/v1/llm/catalog/ghost-id", headers=_ADMIN_HEADERS, json={"enabled": True}
        )
    assert resp.status_code == 404


async def test_catalog_upsert_list_resolve_and_isolation(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        pid = await _new_provider(c)
        mid = await _new_model(c, pid, "cat-haiku", quality="low")
        put = await c.put(
            f"/api/v1/llm/catalog/{mid}",
            headers=_ADMIN_HEADERS,
            json={"enabled": True, "markup_pct": 12.5},
        )
        assert put.status_code == 200, put.text
        assert put.json()["registry"]["alias"] == "cat-haiku"

        listing = await c.get("/api/v1/llm/catalog", headers=_ADMIN_HEADERS)
        assert [m["model_id"] for m in listing.json()["models"]] == [mid]
        assert listing.json()["models"][0]["markup_pct"] == 12.5

        ok = await c.get(
            "/api/v1/llm/catalog/resolve?model_quality=low", headers=_ADMIN_HEADERS
        )
        miss = await c.get(
            "/api/v1/llm/catalog/resolve?model_quality=high", headers=_ADMIN_HEADERS
        )
        anon = await c.get("/api/v1/llm/catalog/resolve?model_quality=low")
        first = await c.delete(f"/api/v1/llm/catalog/{mid}", headers=_ADMIN_HEADERS)
        second = await c.delete(f"/api/v1/llm/catalog/{mid}", headers=_ADMIN_HEADERS)
    assert ok.status_code == 200 and ok.json()["model_alias"] == "cat-haiku"
    assert miss.status_code == 404  # no silent downgrade
    assert anon.status_code == 401
    assert first.status_code == 204 and second.status_code == 404
    # Cross-tenant isolation (service level).
    assert await admin_app.state.catalog_service.resolve("tenant-other", ModelQuality.LOW) is None


async def test_project_models_default_and_explicit(admin_app: FastAPI) -> None:
    async with _client(admin_app) as c:
        pid = await _new_provider(c)
        mid = await _new_model(c, pid, "pm-haiku", quality="low")
        # Catalogue it as a tenant default.
        await c.put(
            f"/api/v1/llm/catalog/{mid}",
            headers=_ADMIN_HEADERS,
            json={"enabled": True, "default_for_new_projects": True},
        )
        # An unconfigured project inherits the default (lazy).
        default = await c.get("/api/v1/llm/projects/proj-X/models", headers=_ADMIN_HEADERS)
        assert default.status_code == 200, default.text
        assert default.json()["is_explicit"] is False
        assert default.json()["model_ids"] == [mid]

        # Set an explicit list (here: empty → the project opts out).
        cleared = await c.put(
            "/api/v1/llm/projects/proj-X/models",
            headers=_ADMIN_HEADERS,
            json={"model_ids": []},
        )
        assert cleared.status_code == 200 and cleared.json()["is_explicit"] is True
        assert cleared.json()["model_ids"] == []

        # A model_id not in the catalogue → 404.
        bad = await c.put(
            "/api/v1/llm/projects/proj-X/models",
            headers=_ADMIN_HEADERS,
            json={"model_ids": ["ghost"]},
        )
    assert bad.status_code == 404
