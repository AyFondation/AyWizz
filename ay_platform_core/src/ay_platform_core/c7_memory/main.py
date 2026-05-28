# =============================================================================
# File: main.py
# Version: 3
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

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.base import EmbeddingProvider
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.embedding.ollama import OllamaEmbedder
from ay_platform_core.c7_memory.kg.repository import KGRepository
from ay_platform_core.c7_memory.router import router
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c7_memory.storage.minio_storage import MemorySourceStorage
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings as C8ClientSettings
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def _build_embedder(cfg: MemoryConfig) -> EmbeddingProvider:
    """Select the embedding adapter declared by ``C7_EMBEDDING_ADAPTER``.

    Adding a new adapter means adding a new value here + a corresponding
    import above. Unknown values fail fast at startup rather than silently
    falling back.
    """
    name = cfg.embedding_adapter
    if name == "deterministic-hash":
        return DeterministicHashEmbedder(
            model_id=cfg.embedding_model_id, dimension=cfg.embedding_dimension
        )
    if name == "ollama":
        return OllamaEmbedder(
            base_url=cfg.ollama_url,
            model_id=cfg.embedding_model_id,
            request_timeout_s=cfg.embedding_ollama_timeout_s,
        )
    raise ValueError(
        f"unknown C7 embedding adapter {name!r}. "
        f"Accepted: 'deterministic-hash', 'ollama'."
    )


def create_app(config: MemoryConfig | None = None) -> FastAPI:
    cfg = config or MemoryConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c7_memory", settings=log_cfg)
    arango_client = ArangoClient(hosts=cfg.arango_url)
    db = arango_client.db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = MemoryRepository(db)
    embedder = _build_embedder(cfg)
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
    llm_client = LLMGatewayClient(
        C8ClientSettings(),
        bearer_token=None,
    )
    service = MemoryService(
        config=cfg,
        repo=repo,
        embedder=embedder,
        storage=storage,
        kg_repo=kg_repo,
        llm_client=llm_client,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo._ensure_collections_sync()
        await storage.ensure_bucket()
        await kg_repo.ensure_collections()
        yield
        # The Ollama adapter owns its httpx client; close it cleanly on
        # shutdown. Other adapters are no-op.
        aclose = getattr(embedder, "aclose", None)
        if aclose is not None:
            await aclose()
        await llm_client.aclose()

    app = FastAPI(title="C7 Memory Service", lifespan=lifespan)
    app.add_middleware(
        AuthGuardMiddleware,
        component="c7_memory",
        exempt_prefixes=["/health", "/api/v1/memory/health"],
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router)
    app.state.memory_service = service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c7_memory"}

    return app


app = create_app()
