# =============================================================================
# File: main.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c8_admin/main.py
# Description: FastAPI app factory for the C8 admin tier (R-100-114). Hosts the
#              platform LLM registry admin surface behind Traefik forward-auth.
#              Deployed via the shared image with `COMPONENT_MODULE=c8_admin`
#              (uvicorn `ay_platform_core.c8_admin.main:app`).
#
#              Wires: Arango registry repository + SecretCipher (master key
#              from env) + LLMRegistryService. On startup it ensures the
#              collection and idempotently seeds missing models from the
#              canonical LiteLLM config (prices/capabilities), without ever
#              seeding a key (env fallback until an operator sets one).
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c8_admin.config import C8AdminConfig
from ay_platform_core.c8_llm.config import LiteLLMConfig
from ay_platform_core.c8_llm.quota.repository import QuotaRepository
from ay_platform_core.c8_llm.quota.router import router as quota_router
from ay_platform_core.c8_llm.quota.service import QuotaService
from ay_platform_core.c8_llm.registry.catalog_repository import TenantCatalogRepository
from ay_platform_core.c8_llm.registry.catalog_router import router as catalog_router
from ay_platform_core.c8_llm.registry.catalog_service import TenantCatalogService
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
from ay_platform_core.c8_llm.registry.embedding_reembed_notifier import ReembedNotifier
from ay_platform_core.c8_llm.registry.embedding_repository import (
    EmbeddingModelRepository,
    EmbeddingProviderRepository,
)
from ay_platform_core.c8_llm.registry.embedding_router import router as embedding_router
from ay_platform_core.c8_llm.registry.embedding_service import (
    EmbeddingModelService,
    EmbeddingProviderService,
)
from ay_platform_core.c8_llm.registry.provider_repository import LLMProviderRepository
from ay_platform_core.c8_llm.registry.provider_router import router as provider_router
from ay_platform_core.c8_llm.registry.provider_service import LLMProviderService
from ay_platform_core.c8_llm.registry.repository import LLMRegistryRepository
from ay_platform_core.c8_llm.registry.router import router as registry_router
from ay_platform_core.c8_llm.registry.seed import seed_missing
from ay_platform_core.c8_llm.registry.service import LLMRegistryService
from ay_platform_core.c8_llm.storage.metering import StorageMeter
from ay_platform_core.c8_llm.storage.repository import StorageSnapshotRepository
from ay_platform_core.c8_llm.storage.router import router as storage_router
from ay_platform_core.c8_llm.storage.service import StorageService
from ay_platform_core.crypto.secret_cipher import SecretCipher, SecretCipherError
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def _cipher_from_env() -> SecretCipher | None:
    """Build the SecretCipher from the env master key, or None when absent.
    None disables key WRITES (503) while keeping read/catalogue/resolve live."""
    import logging  # noqa: PLC0415 — cold path

    try:
        return SecretCipher.from_env()
    except SecretCipherError:
        logging.getLogger("c8_admin").warning(
            "AY_SECRET_MASTER_KEY absent — registry key management disabled "
            "(list / catalogue / resolve still served)"
        )
        return None


def _load_litellm_config(path: str) -> LiteLLMConfig | None:
    """Validate the mounted LiteLLM config for seeding. None when unset or
    unreadable — seeding is then skipped (the registry stays empty until
    populated via the API)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return LiteLLMConfig.model_validate(raw)


def create_app(  # noqa: PLR0915 - cohesive app factory: repos + services + routers
    config: C8AdminConfig | None = None,
    *,
    cipher: SecretCipher | None = None,
) -> FastAPI:
    cfg = config or C8AdminConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c8_admin", settings=log_cfg)
    db = ArangoClient(hosts=cfg.arango_url).db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password,
    )
    repo = LLMRegistryRepository(db)
    provider_repo = LLMProviderRepository(db)
    catalog_repo = TenantCatalogRepository(db)
    # The master key lives ONLY in the environment (Tier-2 .env.secret /
    # K8s Secret). When ABSENT the app still boots and serves read / catalogue /
    # resolve ; only provider-key WRITES are disabled (503). The KEY now lives on
    # the PROVIDER (one credential per endpoint), so the cipher is wired into the
    # provider service ; the model registry holds no secret.
    secret_cipher = cipher if cipher is not None else _cipher_from_env()
    service = LLMRegistryService(repo)
    provider_service = LLMProviderService(provider_repo, secret_cipher)
    catalog_service = TenantCatalogService(catalog_repo, service)
    quota_service = QuotaService(QuotaRepository(db))

    # EMBEDDING registry (D-011) — parallel to the chat registry. Same cipher
    # (one master key). Providers/models are platform_manager-managed; the tenant
    # catalogue + per-project selection are admin/tenant_admin-managed; C7
    # resolves the embedder per project (B4).
    emb_provider_repo = EmbeddingProviderRepository(db)
    emb_model_repo = EmbeddingModelRepository(db)
    emb_catalog_repo = EmbeddingCatalogRepository(db)
    emb_project_repo = EmbeddingProjectRepository(db)
    embedding_provider_service = EmbeddingProviderService(
        emb_provider_repo, emb_model_repo, secret_cipher
    )
    # D-011 / R-400-222 — best-effort re-embed trigger. Fires the C12/n8n
    # webhook when a model edit changes vectors or a project switches
    # selection. Blank URL → disabled (no-op).
    reembed_notifier = ReembedNotifier(
        webhook_url=cfg.reembed_webhook_url,
        timeout_s=cfg.reembed_webhook_timeout_s,
    )
    # project_repo wires the DELETE-GUARD (a model selected by a project can't
    # be deleted) AND enumerates projects to re-embed on a model change.
    embedding_model_service = EmbeddingModelService(
        emb_model_repo, emb_provider_repo, emb_project_repo, reembed_notifier
    )
    embedding_catalog_service = EmbeddingCatalogService(
        emb_catalog_repo, emb_project_repo, emb_model_repo, reembed_notifier
    )

    # Storage metering (E-100-002 v7) — optional: only when a MinIO endpoint is
    # configured. Absent → the storage endpoints answer 503, nothing else
    # changes. Uses the same bucket + credentials as C4's artifact store.
    storage_repo = StorageSnapshotRepository(db)
    storage_service: StorageService | None = None
    if cfg.minio_endpoint:
        from minio import Minio  # noqa: PLC0415 — optional dependency, cold path

        minio_client = Minio(
            cfg.minio_endpoint,
            access_key=cfg.minio_access_key,
            secret_key=cfg.minio_secret_key,
            secure=cfg.minio_secure,
        )
        storage_service = StorageService(
            StorageMeter(minio_client, cfg.minio_bucket), storage_repo
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await repo.ensure_collections()
        await provider_repo.ensure_collections()
        await catalog_repo.ensure_collections()
        await storage_repo.ensure_collections()
        await emb_provider_repo.ensure_collections()
        await emb_model_repo.ensure_collections()
        await emb_catalog_repo.ensure_collections()
        await emb_project_repo.ensure_collections()
        if cfg.seed_on_start:
            litellm_cfg = _load_litellm_config(cfg.litellm_config_path)
            if litellm_cfg is not None:
                await seed_missing(repo, provider_repo, litellm_cfg)
        yield
        await reembed_notifier.aclose()

    app = FastAPI(title="C8 Admin", lifespan=lifespan)
    app.add_middleware(
        AuthGuardMiddleware,
        component="c8_admin",
        exempt_prefixes=["/health"],
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(registry_router)
    app.include_router(provider_router)
    app.include_router(catalog_router)
    app.include_router(quota_router)
    app.include_router(storage_router)
    app.include_router(embedding_router)
    app.include_router(embedding_catalog_router)
    app.state.registry_service = service
    app.state.provider_service = provider_service
    app.state.embedding_provider_service = embedding_provider_service
    app.state.embedding_model_service = embedding_model_service
    app.state.embedding_catalog_service = embedding_catalog_service
    app.state.catalog_service = catalog_service
    app.state.quota_service = quota_service
    app.state.storage_service = storage_service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c8_admin"}

    return app


app = create_app()
