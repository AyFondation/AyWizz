# =============================================================================
# File: main.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c16_backup/main.py
# Description: FastAPI app factory for the C16 Backup/Restore tier (D-022).
#              Deployed via the shared image with COMPONENT_MODULE=c16_backup.
#              Wires the shared ArangoDB + MinIO, the backups bucket, the
#              record registry, and the HTTP surface behind C1 forward-auth.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c16_backup.config import BackupConfig
from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage
from ay_platform_core.c16_backup.router import router
from ay_platform_core.c16_backup.service import BackupService
from ay_platform_core.observability import TraceContextMiddleware, configure_logging
from ay_platform_core.observability.config import LoggingSettings


def create_app(config: BackupConfig | None = None) -> FastAPI:
    cfg = config or BackupConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c16_backup", settings=log_cfg)

    db = ArangoClient(hosts=cfg.arango_url).db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password,
    )
    minio = Minio(
        cfg.minio_endpoint, access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key, secure=cfg.minio_secure,
    )
    records = BackupRecordRepository(db)
    storage = BackupStorage(minio, cfg.backups_bucket)
    service = BackupService(
        db=db, minio=minio, storage=storage, records=records,
        platform_version=cfg.platform_version,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        records.ensure_collections()
        # Best-effort: the backups bucket is created by the minio-init Job. A
        # transient MinIO hiccup / startup-order race here must NOT crash-loop
        # the pod — the service ensures the bucket lazily before every write.
        import contextlib  # noqa: PLC0415 - cold path

        with contextlib.suppress(Exception):
            storage.ensure_bucket()
        yield

    app = FastAPI(title="C16 Backup/Restore", lifespan=lifespan)
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.state.backup_service = service
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "c16_backup"}

    return app


app = create_app()
