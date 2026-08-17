# =============================================================================
# File: snapshot_job.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/storage/snapshot_job.py
# Description: Metering entrypoint for the periodic storage snapshot pass
#              (E-100-002 v7). Runs OUT of band (a K8s CronJob) against MinIO +
#              Arango directly — no HTTP, no forward-auth — appending one
#              `storage_snapshots` row per project. Invoke:
#                python -m ay_platform_core.c8_llm.storage.snapshot_job
# @relation implements:R-100-140
# =============================================================================

from __future__ import annotations

import asyncio

from ay_platform_core.c8_llm.storage.models import StorageSnapshotResult
from ay_platform_core.c8_llm.storage.service import StorageService


async def run_once(service: StorageService) -> StorageSnapshotResult:
    """Run a single metering pass. Extracted for testability — the CronJob's
    `__main__` builds the service from env and calls this."""
    return await service.run_snapshot()


def _build_service() -> StorageService | None:  # pragma: no cover - infra wiring
    """Construct the StorageService from the C8-admin env (Arango + MinIO).
    Returns None when MinIO is unconfigured (metering disabled)."""
    from arango import ArangoClient  # type: ignore[attr-defined] # noqa: PLC0415
    from minio import Minio  # noqa: PLC0415

    from ay_platform_core.c8_admin.config import C8AdminConfig  # noqa: PLC0415
    from ay_platform_core.c8_llm.storage.metering import StorageMeter  # noqa: PLC0415
    from ay_platform_core.c8_llm.storage.repository import (  # noqa: PLC0415
        StorageSnapshotRepository,
    )

    cfg = C8AdminConfig()
    if not cfg.minio_endpoint:
        return None
    db = ArangoClient(hosts=cfg.arango_url).db(
        cfg.arango_db, username=cfg.arango_username, password=cfg.arango_password
    )
    repo = StorageSnapshotRepository(db)
    client = Minio(
        cfg.minio_endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=cfg.minio_secure,
    )
    return StorageService(StorageMeter(client, cfg.minio_bucket), repo)


async def _main() -> None:  # pragma: no cover - infra wiring
    service = _build_service()
    if service is None:
        print("storage snapshot: MinIO unconfigured — skipped")
        return
    # The dashboards call ensure_collections at app start, but the CronJob may
    # run before the app; ensure the collection exists here too.
    repo = service._repo
    ensure = getattr(repo, "ensure_collections", None)
    if ensure is not None:
        await ensure()
    result = await run_once(service)
    print(
        f"storage snapshot: wrote {result.snapshots_written} project snapshot(s) "
        f"at {result.measured_at}"
    )


if __name__ == "__main__":  # pragma: no cover - infra entrypoint
    asyncio.run(_main())
