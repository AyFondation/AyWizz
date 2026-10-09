# =============================================================================
# File: main.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c16_backup/main.py
# Description: FastAPI app factory for the C16 Backup/Restore tier (D-022).
#              Deployed via the shared image with COMPONENT_MODULE=c16_backup.
#              Wires the shared ArangoDB + MinIO, the backups bucket, the
#              record registry, and the HTTP surface behind C1 forward-auth.
#              v3 (2026-10-09): `describe_app` enriches the
#              generated OpenAPI document — app description + real
#              version, the gateway-injected identity headers hidden
#              (the document was advertising them as caller-supplied),
#              and the derivable 401 / 404 responses declared.
#
# @relation implements:R-100-114
# =============================================================================

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.api_docs import describe_app, docs_urls
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

    app = FastAPI(title="C16 Backup/Restore", lifespan=lifespan, **docs_urls("c16-backup"))
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.state.backup_service = service
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe for the kubelet (R-100-114).

        Answers `ok` whenever the process serves requests, and
        deliberately checks NO dependency: a probe that fails because
        ArangoDB is slow takes the pod out of service for a condition
        restarting it cannot fix. Reachable without a token — the
        kubelet has none.
        """
        return {"status": "ok", "component": "c16_backup"}

    describe_app(
        app,
        summary="Per-project snapshot and restore.",
        description="""
C16 takes a point-in-time snapshot of one project — its documents,
requirements, sources, chunks, graph and conversations — and restores one
into the same project or a different one.

### Restore is a decision, not a convenience

`dry_run` is the default posture for a reason: a restore overwrites live
content, and the report tells you how many records of each kind would be
touched before any of them are. Restoring into a different project is
supported and is how a snapshot is cloned; the report then names the source
project so the two cannot be confused afterwards.

### A snapshot is not a database backup

It covers one project's content as the platform models it. It does not
cover tenants, users, role grants, provider credentials or quota policy —
those are platform state, not project state.
""",
    )

    return app


app = create_app()
