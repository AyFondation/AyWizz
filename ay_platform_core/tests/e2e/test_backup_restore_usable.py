# =============================================================================
# File: test_backup_restore_usable.py
# Version: 1
# Path: ay_platform_core/tests/e2e/test_backup_restore_usable.py
# Description: Cross-component e2e (D-022): prove a RESTORED project is USABLE by
#              another component. Seed C7 with a source (→ memory_sources +
#              memory_chunks), snapshot the project with C16, restore-as-new,
#              then run a C7 RETRIEVAL on the NEW project and assert it finds the
#              restored source — the round-trip preserved not just the bytes but
#              the project's working RAG state.
#
# @relation validates:R-900-008
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from minio import Minio

from ay_platform_core.c7_memory.config import MemoryConfig
from ay_platform_core.c7_memory.db.repository import MemoryRepository
from ay_platform_core.c7_memory.embedding.deterministic import DeterministicHashEmbedder
from ay_platform_core.c7_memory.models import (
    IndexKind,
    RetrievalRequest,
    SourceIngestRequest,
)
from ay_platform_core.c7_memory.service import MemoryService
from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage
from ay_platform_core.c16_backup.service import BackupService
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="function")]

_T = "tenant-e2e"
_P = "proj-e2e"


@pytest_asyncio.fixture(scope="function")
async def stack(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint,
) -> AsyncIterator[dict[str, Any]]:
    db_name = f"c16e2e_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    # C7 memory service (deterministic embedder — the archived VECTORS are what
    # retrieval scores; embedding quality is irrelevant here).
    c7_repo = MemoryRepository(db)
    c7_repo._ensure_collections_sync()
    c7 = MemoryService(
        config=MemoryConfig(chunk_token_size=64, chunk_overlap=8,
                            default_quota_bytes=1024 * 1024 * 1024,
                            retrieval_scan_cap=1000),
        repo=c7_repo, embedder=DeterministicHashEmbedder(model_id="m1", dimension=64),
    )
    client = Minio(
        minio_container.endpoint, access_key=minio_container.access_key,
        secret_key=minio_container.secret_key, secure=False,
    )
    bucket = f"backups-{uuid.uuid4().hex[:8]}"
    records = BackupRecordRepository(db)
    records.ensure_collections()
    c16 = BackupService(
        db=db, minio=client, storage=BackupStorage(client, bucket), records=records)
    try:
        yield {"c7": c7, "c16": c16, "client": client, "bucket": bucket}
    finally:
        cleanup_minio_bucket(minio_container, bucket)
        cleanup_arango_database(arango_container, db_name)


async def test_restored_project_is_retrievable(stack: dict[str, Any]) -> None:
    c7: MemoryService = stack["c7"]
    c16: BackupService = stack["c16"]

    # 1. Seed the source project's RAG state.
    await c7.ingest_source(
        SourceIngestRequest(
            source_id="src-voyager", project_id=_P, mime_type="text/plain",
            content="The Voyager 1 spacecraft was launched in 1977 by NASA.",
            size_bytes=64, uploaded_by="alice",
        ),
        tenant_id=_T,
    )
    # Sanity: retrievable in the SOURCE project.
    src_hits = (await c7.retrieve(
        RetrievalRequest(project_id=_P, query="Voyager launch",
                         indexes=[IndexKind.EXTERNAL_SOURCES], top_k=5),
        tenant_id=_T,
    )).hits
    assert src_hits, "seed not retrievable in source project"

    # 2. Snapshot + restore-as-new (same tenant, fresh project).
    record = c16.create_project_snapshot(tenant_id=_T, project_id=_P, created_by="alice")
    gz = BackupStorage(stack["client"], stack["bucket"]).get(record.object_key)
    report = c16.restore_project_as_new(gz, target_tenant_id=_T, dry_run=False)
    new_pid = report.new_project_id
    assert new_pid and new_pid != _P

    # 3. The RESTORED project is USABLE: a C7 retrieval on the NEW project id
    #    finds the restored source (vectors + chunks came across intact).
    hits = (await c7.retrieve(
        RetrievalRequest(project_id=new_pid, query="Voyager launch",
                         indexes=[IndexKind.EXTERNAL_SOURCES], top_k=5),
        tenant_id=_T,
    )).hits
    assert hits, "restored project has no retrievable content"
    assert any("Voyager" in h.content for h in hits)
