# =============================================================================
# File: test_embedding_registry_api.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_admin/test_embedding_registry_api.py
# Description: Functional (integration) tests for the platform EMBEDDING
#              registry HTTP surface against a real ArangoDB — the CRUD path a
#              platform_manager drives: providers (endpoint + write-only key),
#              models (upstream + dimension), the provider DELETE-GUARD, and the
#              role gate. Covers every /admin/v1/llm/embedding-* endpoint.
#
# @relation validates:R-400-226
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_llm.registry.embedding_catalog_repository import (
    EmbeddingCatalogRepository,
    EmbeddingProjectRepository,
)
from ay_platform_core.c8_llm.registry.embedding_catalog_router import (
    router as embedding_catalog_router,
)
from ay_platform_core.c8_llm.registry.embedding_catalog_service import (
    EmbeddingCatalogService,
)
from ay_platform_core.c8_llm.registry.embedding_repository import (
    EmbeddingModelRepository,
    EmbeddingProviderRepository,
)
from ay_platform_core.c8_llm.registry.embedding_router import router as embedding_router
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelService,
    EmbeddingProviderService,
)
from ay_platform_core.crypto.secret_cipher import SecretCipher
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TMGR = {"X-User-Id": "u-tmgr", "X-User-Roles": "platform_manager"}
_USER = {"X-User-Id": "u-plain", "X-User-Roles": "user"}
_ADMIN = {"X-User-Id": "u-admin", "X-User-Roles": "admin", "X-Tenant-Id": "tenant-1"}
_PROVIDER = {"name": "Ollama", "adapter": "ollama", "base_url": "http://ollama:11434"}


def _model_body(provider_id: str, alias: str, dim: int = 384) -> dict[str, object]:
    return {
        "alias": alias,
        "provider_id": provider_id,
        "upstream_model": "all-minilm",
        "dimension": dim,
        "enabled": True,
    }


@pytest_asyncio.fixture(scope="function")
async def emb_app(arango_container: ArangoEndpoint) -> AsyncIterator[FastAPI]:
    db_name = f"c8_emb_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    prov_repo = EmbeddingProviderRepository(db)
    await prov_repo.ensure_collections()
    model_repo = EmbeddingModelRepository(db)
    await model_repo.ensure_collections()
    catalog_repo = EmbeddingCatalogRepository(db)
    await catalog_repo.ensure_collections()
    project_repo = EmbeddingProjectRepository(db)
    await project_repo.ensure_collections()
    cipher = SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")
    app = FastAPI()
    app.include_router(embedding_router)
    app.include_router(embedding_catalog_router)
    app.state.embedding_provider_service = EmbeddingProviderService(
        prov_repo, model_repo, cipher
    )
    app.state.embedding_model_service = EmbeddingModelService(
        model_repo, prov_repo, project_repo
    )
    app.state.embedding_catalog_service = EmbeddingCatalogService(
        catalog_repo, project_repo, model_repo
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


async def test_provider_crud_and_write_only_key(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        created = await c.post(
            "/admin/v1/llm/embedding-providers", headers=_TMGR, json=_PROVIDER
        )
        assert created.status_code == 201, created.text
        pid = created.json()["provider_id"]
        assert created.json()["key_status"] == "not_set"

        keyed = await c.put(
            f"/admin/v1/llm/embedding-providers/{pid}/api-key",
            headers=_TMGR,
            json={"api_key": "sk-emb-secret"},
        )
        assert keyed.status_code == 200
        assert keyed.json()["key_status"] == "set"
        # The ciphertext is NEVER echoed.
        assert "api_key" not in keyed.json()

        updated = await c.put(
            f"/admin/v1/llm/embedding-providers/{pid}",
            headers=_TMGR,
            json={**_PROVIDER, "name": "Ollama-2"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Ollama-2"
        assert updated.json()["key_status"] == "set"  # key preserved on update

        listing = await c.get("/admin/v1/llm/embedding-providers", headers=_TMGR)
        assert listing.status_code == 200
        assert {p["provider_id"] for p in listing.json()["providers"]} == {pid}


async def test_model_crud_and_provider_delete_guard(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        pid = (await c.post(
            "/admin/v1/llm/embedding-providers", headers=_TMGR, json=_PROVIDER
        )).json()["provider_id"]

        created = await c.post(
            "/admin/v1/llm/embedding-models", headers=_TMGR, json=_model_body(pid, "mini")
        )
        assert created.status_code == 201, created.text
        mid = created.json()["model_id"]
        assert created.json()["dimension"] == 384

        models = await c.get("/admin/v1/llm/embedding-models", headers=_TMGR)
        assert {m["model_id"] for m in models.json()["models"]} == {mid}

        updated = await c.put(
            f"/admin/v1/llm/embedding-models/{mid}",
            headers=_TMGR,
            json=_model_body(pid, "mini", dim=768),
        )
        assert updated.status_code == 200
        assert updated.json()["dimension"] == 768

        # DELETE-GUARD: the provider cannot be deleted while the model exists.
        guarded = await c.delete(
            f"/admin/v1/llm/embedding-providers/{pid}", headers=_TMGR
        )
        assert guarded.status_code == 409, guarded.text

        # Remove the model first, then the provider deletes.
        assert (await c.delete(
            f"/admin/v1/llm/embedding-models/{mid}", headers=_TMGR
        )).status_code == 204
        assert (await c.delete(
            f"/admin/v1/llm/embedding-providers/{pid}", headers=_TMGR
        )).status_code == 204


async def test_requires_platform_manager(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        assert (await c.get(
            "/admin/v1/llm/embedding-providers", headers=_USER
        )).status_code == 403
        assert (await c.get("/admin/v1/llm/embedding-providers")).status_code == 401


async def test_model_create_unknown_provider_422(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        resp = await c.post(
            "/admin/v1/llm/embedding-models",
            headers=_TMGR,
            json=_model_body("ghost", "x"),
        )
        assert resp.status_code == 422, resp.text


async def _provider_and_model(c: httpx.AsyncClient, alias: str = "mini") -> str:
    pid = (await c.post(
        "/admin/v1/llm/embedding-providers", headers=_TMGR, json=_PROVIDER
    )).json()["provider_id"]
    resp = await c.post(
        "/admin/v1/llm/embedding-models", headers=_TMGR, json=_model_body(pid, alias)
    )
    return str(resp.json()["model_id"])


async def test_catalog_default_and_project_selection(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        mid = await _provider_and_model(c)

        # Tenant exposes the model as its default for new projects.
        up = await c.put(
            f"/api/v1/llm/embedding-catalog/{mid}",
            headers=_ADMIN,
            json={"enabled": True, "default_for_new_projects": True},
        )
        assert up.status_code == 200, up.text
        assert up.json()["default_for_new_projects"] is True

        listing = await c.get("/api/v1/llm/embedding-catalog", headers=_ADMIN)
        assert {m["model_id"] for m in listing.json()["models"]} == {mid}

        # A fresh project inherits the tenant default (lazy, is_explicit False).
        got = await c.get("/api/v1/llm/projects/proj-A/embedding", headers=_ADMIN)
        assert got.json()["model_id"] == mid
        assert got.json()["is_explicit"] is False

        # Explicit selection.
        sel = await c.put(
            "/api/v1/llm/projects/proj-A/embedding", headers=_ADMIN, json={"model_id": mid}
        )
        assert sel.status_code == 200
        assert sel.json()["is_explicit"] is True
        assert sel.json()["model"]["dimension"] == 384


async def test_catalog_available_picker(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        mid = await _provider_and_model(c)  # registered, not yet catalogued
        avail = await c.get("/api/v1/llm/embedding-catalog/available", headers=_ADMIN)
        assert avail.status_code == 200, avail.text
        assert mid in {m["model_id"] for m in avail.json()["models"]}
        # Once catalogued, it drops out of the available picker.
        await c.put(
            f"/api/v1/llm/embedding-catalog/{mid}", headers=_ADMIN, json={"enabled": True}
        )
        avail2 = await c.get("/api/v1/llm/embedding-catalog/available", headers=_ADMIN)
        assert mid not in {m["model_id"] for m in avail2.json()["models"]}


async def test_catalog_upsert_unknown_model_404(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        resp = await c.put(
            "/api/v1/llm/embedding-catalog/ghost",
            headers=_ADMIN,
            json={"enabled": True},
        )
        assert resp.status_code == 404, resp.text


async def test_project_select_non_catalogued_422(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        mid = await _provider_and_model(c)  # registered, but NOT in the tenant catalogue
        resp = await c.put(
            "/api/v1/llm/projects/proj-A/embedding", headers=_ADMIN, json={"model_id": mid}
        )
        assert resp.status_code == 422, resp.text


async def test_delete_guards_protect_projects(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        mid = await _provider_and_model(c)
        await c.put(
            f"/api/v1/llm/embedding-catalog/{mid}", headers=_ADMIN, json={"enabled": True}
        )
        await c.put(
            "/api/v1/llm/projects/proj-A/embedding", headers=_ADMIN, json={"model_id": mid}
        )
        # Catalogue removal is guarded while a project selects the model.
        rm = await c.delete(f"/api/v1/llm/embedding-catalog/{mid}", headers=_ADMIN)
        assert rm.status_code == 409, rm.text
        # Platform-level model delete is likewise guarded.
        dm = await c.delete(f"/admin/v1/llm/embedding-models/{mid}", headers=_TMGR)
        assert dm.status_code == 409, dm.text


async def test_catalog_requires_admin_not_platform_manager(emb_app: FastAPI) -> None:
    async with _client(emb_app) as c:
        # platform_manager is EXCLUDED from the content (tenant) surface.
        pm = await c.get(
            "/api/v1/llm/embedding-catalog",
            headers={**_TMGR, "X-Tenant-Id": "tenant-1"},
        )
        assert pm.status_code == 403
        anon = await c.get("/api/v1/llm/embedding-catalog")
        assert anon.status_code == 401
