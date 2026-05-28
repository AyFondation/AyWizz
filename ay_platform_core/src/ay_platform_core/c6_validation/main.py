# =============================================================================
# File: main.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c6_validation/main.py
# Description: FastAPI app factory for C6 Validation Pipeline Registry.
#
#              v2 (D-017 / R-700-032): wires a C8 LLM gateway client into the
#              service so the opt-in T3 LLM-as-judge can run when
#              `C6_JUDGE_ENABLED=true`. The client is built unconditionally
#              (cheap, no network at construction) like every other component ;
#              the judge stays dormant until the flag is set.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

# Importing the package triggers registration of the built-in `code` plugin
# (R-700-002 — build-time discovery).
import ay_platform_core.c6_validation  # noqa: F401
from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.db.repository import ValidationRepository
from ay_platform_core.c6_validation.plugin.registry import get_registry
from ay_platform_core.c6_validation.router import router
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings as C8ClientSettings
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def create_app(config: ValidationConfig | None = None) -> FastAPI:
    cfg = config or ValidationConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c6_validation", settings=log_cfg)
    arango_client = ArangoClient(hosts=cfg.arango_url)
    db = arango_client.db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = ValidationRepository(db)

    minio_client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )
    snapshot_store = ValidationSnapshotStorage(minio_client, cfg.minio_bucket)

    # C8 client for the opt-in T3 LLM-as-judge (R-700-032). Reads its config
    # from env like every other component ; the judge is gated by
    # `cfg.judge_enabled` so this client is idle unless the flag is on.
    llm_client = LLMGatewayClient(C8ClientSettings(), bearer_token=None)

    service = ValidationService(
        config=cfg,
        registry=get_registry(),
        repo=repo,
        snapshot_store=snapshot_store,
        llm_client=llm_client,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo._ensure_collections_sync()
        snapshot_store._ensure_bucket_sync()
        yield
        await llm_client.aclose()

    app = FastAPI(title="C6 Validation Pipeline Registry", lifespan=lifespan)
    # `/api/v1/validation/health` is a public status endpoint that
    # K8s probes / smoke tests hit without auth — exempt explicitly.
    # In K8s, Traefik forward-auth still gates it at the edge.
    app.add_middleware(
        AuthGuardMiddleware,
        component="c6_validation",
        exempt_prefixes=["/health", "/api/v1/validation/health"],
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router)
    app.state.validation_service = service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c6_validation"}

    return app


app = create_app()
