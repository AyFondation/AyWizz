# =============================================================================
# File: main.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c7_memory/main.py
# Description: FastAPI app factory for C7 Memory Service. v3 wires
#              `MemorySourceStorage` (MinIO blob storage for uploaded
#              source files) — required by the multipart upload
#              endpoint added in Phase B of the v1 functional plan.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c7_memory.c12_client import C12WebhookClient
from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.base import EmbeddingProvider
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.embedding.registry_resolver import (
    build_embedding_resolver,
)
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.llm_resolver import LLMResolverClient
from ay_platform_core.c7_memory.router import router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings as C8ClientSettings
from ay_platform_core.c8_llm.quota.guard import build_quota_guard
from ay_platform_core.c8_llm.quota.http import register_quota_handler
from ay_platform_core.c8_llm.registry.key_provider import build_registry_key_provider
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def _build_embedder() -> EmbeddingProvider:
    """The C7 env-level BOOTSTRAP fallback embedder (D-011).

    Embedding selection is in-app: real embedders (ollama, openai-compatible,
    …) are declared in the embedding registry and resolved per-project by
    `RegistryEmbedderResolver`. This fallback is used ONLY when a project has
    no registry selection. It is deliberately a fixed, keyless, no-network
    deterministic hash — no provider adherence, no config knob — so a fresh
    install works out of the box (lexical RAG) until the operator configures a
    real embedding model via the HMI.
    """
    return DeterministicHashEmbedder()


def create_app(config: MemoryConfig | None = None) -> FastAPI:
    cfg = config or MemoryConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c7_memory", settings=log_cfg)
    arango_client = ArangoClient(hosts=cfg.arango_url)
    db = arango_client.db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = MemoryRepository(db)
    embedder = _build_embedder()
    minio_client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )
    storage = MemorySourceStorage(minio_client, cfg.minio_bucket)
    kg_repo = KGRepository(db)
    # Phase F.1 — C8 LLM gateway client for KG extraction. Reads its
    # config from env (`C8_GATEWAY_URL`, etc.) the same way every
    # other component talks to C8.
    # LLM-governance #3 (option B) — per-call upstream-key injection from the
    # platform registry (shared Arango `db`). None when no master key is set
    # → proxy env key is used (non-breaking fallback).
    llm_client = LLMGatewayClient(
        C8ClientSettings(),
        bearer_token=None,
        key_provider=build_registry_key_provider(db),
        quota_guard=build_quota_guard(db),
    )
    # R-100-081 v3 — outbound trigger to the C12 (n8n) ingestion webhook.
    c12_client = C12WebhookClient(
        webhook_url=cfg.c12_webhook_url,
        timeout_s=cfg.c12_webhook_timeout_s,
    )
    # LLM-governance #5 — model_quality → alias resolver (C8 admin catalogue).
    # Disabled (no-op) until `C7_C8_ADMIN_URL` is set to the deployed c8_admin.
    llm_resolver = LLMResolverClient(
        base_url=cfg.c8_admin_url,
        timeout_s=cfg.c8_admin_timeout_s,
    )
    # D-011 — per-project embedder resolution from the in-app embedding
    # registry (shared Arango `db`). `embedder` above stays the global
    # fallback used when a project has no registry selection.
    embedder_resolver = build_embedding_resolver(db)
    service = MemoryService(
        config=cfg,
        repo=repo,
        embedder=embedder,
        storage=storage,
        kg_repo=kg_repo,
        llm_client=llm_client,
        c12_client=c12_client,
        llm_resolver=llm_resolver,
        embedder_resolver=embedder_resolver,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo._ensure_collections_sync()
        await storage.ensure_bucket()
        await kg_repo.ensure_collections()
        await embedder_resolver.ensure_collections()
        yield
        # The Ollama adapter owns its httpx client; close it cleanly on
        # shutdown. Other adapters are no-op.
        aclose = getattr(embedder, "aclose", None)
        if aclose is not None:
            await aclose()
        await llm_client.aclose()
        await c12_client.aclose()
        await llm_resolver.aclose()

    app = FastAPI(title="C7 Memory Service", lifespan=lifespan)
    app.add_middleware(
        AuthGuardMiddleware,
        component="c7_memory",
        exempt_prefixes=["/health", "/api/v1/memory/health"],
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    register_quota_handler(app)  # QuotaExceededError → 429
    app.include_router(router)
    app.state.memory_service = service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c7_memory"}

    return app


app = create_app()
