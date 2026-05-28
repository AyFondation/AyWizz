# =============================================================================
# File: main.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/main.py
# Description: FastAPI app factory for C5 Requirements Service.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c5_requirements.config import RequirementsConfig
from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.events.null_publisher import NullPublisher
from ay_platform_core.c5_requirements.router import router
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings


def create_app(config: RequirementsConfig | None = None) -> FastAPI:
    cfg = config or RequirementsConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c5_requirements", settings=log_cfg)
    arango_client = ArangoClient(hosts=cfg.arango_url)
    db = arango_client.db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = RequirementsRepository(db)

    minio_client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )
    storage = RequirementsStorage(minio_client, cfg.minio_bucket)
    service = RequirementsService(repo, storage, NullPublisher())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo._ensure_collections_sync()
        storage._ensure_bucket_sync()
        yield

    app = FastAPI(title="C5 Requirements Service", lifespan=lifespan)
    app.add_middleware(AuthGuardMiddleware, component="c5_requirements")
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router)
    app.state.requirements_service = service

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c5_requirements"}

    return app


app = create_app()
