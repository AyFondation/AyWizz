# =============================================================================
# File: test_upload_model_quality_e2e.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_upload_model_quality_e2e.py
# Description: GOLDEN-PATH end-to-end of the LLM-governance #5 ingestion path,
#              composing EVERY real piece a user upload triggers:
#                real Arango (C7 sources + registry + catalogue) + real MinIO
#                (raw blob) + the real C7 MemoryService + the real
#                LLMResolverClient driving the real c8_admin app.
#              A project sets `model_quality`; an upload then resolves it
#              through c8_admin into the C13 `llm_assignments` carried by the
#              C12 trigger. This is the test that proves the WHOLE chain works
#              for real user traffic (only the C12 webhook is captured, and the
#              LLM proxy is never reached at this stage — extraction is async).
# =============================================================================

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.llm_resolver import LLMResolverClient
from ay_platform_core.c7_memory.models import EnrichmentConfig
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_router import router as catalog_router
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
from ay_platform_core.c8_llm.registry.models import (
    LLMModelUpsert,
    ModelCapabilities,
    ModelQuality,
)
from ay_platform_core.c8_llm.registry.provider_models import LLMProviderUpsert
from ay_platform_core.c8_llm.registry.provider_repository import LLMProviderRepository
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.crypto.secret_cipher import SecretCipher
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TENANT = "tenant-up"
_PROJECT = "project-up"
_ADMIN = {"X-User-Id": "u-adm", "X-User-Roles": "admin", "X-Tenant-Id": _TENANT}
_TMGR = {"X-User-Id": "u-tmgr", "X-User-Roles": "platform_manager"}


class _CapturingC12:
    """Fake C12 webhook that records the metadata-only trigger payload."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def trigger_ingestion(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)


def _build_c8_admin(db: Any) -> FastAPI:
    repo = LLMRegistryRepository(db)
    repo._ensure_collections_sync()
    provider_repo = LLMProviderRepository(db)
    provider_repo._ensure_collections_sync()
    catalog_repo = TenantCatalogRepository(db)
    catalog_repo._ensure_collections_sync()
    registry_service = LLMRegistryService(repo)
    cipher = SecretCipher(keys={"k1": b"0" * 32}, active_key_id="k1")
    app = FastAPI()
    app.include_router(catalog_router)
    app.state.registry_service = registry_service
    app.state.provider_service = LLMProviderService(provider_repo, cipher)
    app.state.catalog_service = TenantCatalogService(catalog_repo, registry_service)
    return app


async def _seed_governance(app: FastAPI, alias: str) -> str:
    """Create the provider + model and return the model's stable id."""
    prov = await app.state.provider_service.create_provider(
        LLMProviderUpsert(
            name="Anthropic", base_url="https://api.anthropic.com", wire_format="anthropic"
        )
    )
    public = await app.state.registry_service.create_model(
        LLMModelUpsert(
            alias=alias,
            provider_id=prov.provider_id,
            upstream_model=alias,
            capabilities=ModelCapabilities(
                vision=True, tool_calling=True, context_window=200000
            ),
            provider_cost_in_per_1m=15.0,
            provider_cost_out_per_1m=75.0,
            default_model_quality=ModelQuality.HIGH,
        )
    )
    return str(public.model_id)


async def test_upload_resolves_model_quality_into_c12_trigger(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> None:
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)

    # --- c8_admin governance store (its own logical DB) ---------------------
    admin_db_name = f"e2e_admin_{uuid.uuid4().hex[:8]}"
    sys_db.create_database(admin_db_name)
    admin_db = client.db(admin_db_name, username="root", password=arango_container.password)
    c8_admin_app = _build_c8_admin(admin_db)
    alias = "claude-opus-flagship"
    model_id = await _seed_governance(c8_admin_app, alias)

    # --- C7 store (its own logical DB) + a real MinIO bucket ----------------
    c7_db_name = f"e2e_c7_{uuid.uuid4().hex[:8]}"
    sys_db.create_database(c7_db_name)
    c7_db = client.db(c7_db_name, username="root", password=arango_container.password)
    c7_repo = MemoryRepository(c7_db)
    c7_repo._ensure_collections_sync()

    minio_bucket = f"e2e-up-{uuid.uuid4().hex[:6]}"
    minio_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = MemorySourceStorage(minio_client, minio_bucket)
    storage._ensure_bucket_sync()

    # The REAL C7 resolver, pointed at the REAL c8_admin app over ASGI.
    resolver = LLMResolverClient(
        base_url="http://c8-admin",
        client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=c8_admin_app), base_url="http://c8-admin"
        ),
    )
    c12 = _CapturingC12()
    service = MemoryService(
        config=MemoryConfig(
            embedding_adapter="deterministic-hash",
            embedding_dimension=128,
            default_quota_bytes=1024 * 1024 * 1024,
            c13_artifacts_bucket=minio_bucket,
        ),
        repo=c7_repo,
        embedder=DeterministicHashEmbedder(dimension=128),
        storage=storage,
        c12_client=c12,  # type: ignore[arg-type]
        llm_resolver=resolver,
    )

    try:
        # Admin catalogues the model for the tenant via the REAL c8_admin app.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=c8_admin_app, raise_app_exceptions=False),
            base_url="http://c8-admin",
        ) as admin_http:
            cat = await admin_http.put(
                f"/api/v1/llm/catalog/{model_id}", headers=_ADMIN, json={"enabled": True}
            )
            assert cat.status_code == 200, cat.text

        # Project picks model_quality=high.
        await service.set_project_enrichment_config(
            _TENANT, _PROJECT, EnrichmentConfig(model_quality="high")
        )

        # The user upload.
        await service.store_raw_upload_and_trigger(
            tenant_id=_TENANT,
            project_id=_PROJECT,
            source_id="src-up-1",
            filename="report.pdf",
            mime_type="application/pdf",
            source_format="pdf",
            data=b"%PDF-1.7 bytes",
            uploaded_by="u-adm",
        )

        # The C12 trigger carries the RESOLVED model on every enrichment agent.
        assert len(c12.calls) == 1
        assignments = c12.calls[0]["config_overrides"]["llm_assignments"]
        assert assignments == {
            "summarizer": alias,
            "decontextualizer": alias,
            "densifier": alias,
            "image_analyzer": alias,
        }
    finally:
        await resolver.aclose()
        cleanup_minio_bucket(minio_container, minio_bucket)
        cleanup_arango_database(arango_container, admin_db_name)
        cleanup_arango_database(arango_container, c7_db_name)
