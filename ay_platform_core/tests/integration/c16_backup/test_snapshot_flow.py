# =============================================================================
# File: test_snapshot_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c16_backup/test_snapshot_flow.py
# Description: Integration test for the C16 project-snapshot core (R-900-002/003)
#              against a REAL ArangoDB + MinIO. Seeds a dedicated dataset for two
#              projects of one tenant (+ a second tenant), snapshots ONE project,
#              then parses the stored archive and asserts EXHAUSTIVELY: the right
#              rows/objects are captured, the OTHER project/tenant is excluded
#              (isolation), the record is registered, and the archive checksum
#              matches.
#
# @relation validates:R-900-002
# @relation validates:R-900-003
# =============================================================================

from __future__ import annotations

import hashlib
import io
import re
import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from minio import Minio
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

from ay_platform_core.c16_backup.archive import parse_archive
from ay_platform_core.c16_backup.models import Store
from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage
from ay_platform_core.c16_backup.service import BackupService

pytestmark = pytest.mark.integration

_T = "tenant-bkp"


def _put(client: Minio, bucket: str, key: str, data: bytes) -> None:
    client.put_object(bucket, key, io.BytesIO(data), length=len(data))


@pytest.fixture(scope="function")
def stack(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint,
) -> Iterator[dict[str, object]]:
    db_name = f"c16_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    # Seed two project-scoped collections with rows for p1, p2 (same tenant)
    # and a second tenant — to prove the snapshot captures ONLY (t, p1).
    for coll in ("c3_messages", "memory_sources"):
        db.create_collection(coll)
    db.collection("c3_messages").insert_many([
        {"_key": "m1", "tenant_id": _T, "project_id": "p1", "content": "keep-1"},
        {"_key": "m2", "tenant_id": _T, "project_id": "p1", "content": "keep-2"},
        {"_key": "m3", "tenant_id": _T, "project_id": "p2", "content": "other-project"},
        {"_key": "m4", "tenant_id": "tenant-x", "project_id": "p1", "content": "other-tenant"},
    ])
    db.collection("memory_sources").insert_many([
        {"_key": "s1", "tenant_id": _T, "project_id": "p1", "uri": "keep"},
    ])

    client = Minio(
        minio_container.endpoint, access_key=minio_container.access_key,
        secret_key=minio_container.secret_key, secure=False,
    )
    client.make_bucket("memory")
    _put(client, "memory", f"sources/{_T}/p1/s1/doc.md", b"# keep")
    _put(client, "memory", f"sources/{_T}/p1/s1/raw.bin", b"\x00\x01\x02")
    _put(client, "memory", f"sources/{_T}/p2/s9/other.md", b"# other project")

    backups_bucket = f"backups-{uuid.uuid4().hex[:8]}"
    storage = BackupStorage(client, backups_bucket)
    records = BackupRecordRepository(db)
    records.ensure_collections()
    service = BackupService(
        db=db, minio=client, storage=storage, records=records,
        platform_version="test",
    )
    try:
        yield {"service": service, "storage": storage, "records": records}
    finally:
        cleanup_minio_bucket(minio_container, "memory")
        cleanup_minio_bucket(minio_container, backups_bucket)
        cleanup_arango_database(arango_container, db_name)


def test_project_snapshot_captures_only_that_project(
    stack: dict[str, object],
) -> None:
    service: BackupService = stack["service"]  # type: ignore[assignment]
    storage: BackupStorage = stack["storage"]  # type: ignore[assignment]
    records: BackupRecordRepository = stack["records"]  # type: ignore[assignment]

    record = service.create_project_snapshot(
        tenant_id=_T, project_id="p1", created_by="alice",
    )

    # ---- Record persisted + pointer + checksum -----------------------------
    assert record.project_id == "p1"
    assert record.origin.value == "generated"
    # Naming rule: filename ends with an ISO date postfix `_yyyymmdd_hhmmss`.
    assert re.fullmatch(
        rf"tenant/{_T}/project/p1/[0-9a-f]+_\d{{8}}_\d{{6}}\.tar\.gz",
        record.object_key,
    ), record.object_key
    fetched = records.get(record.backup_id)
    assert fetched is not None and fetched.object_key == record.object_key

    gz = storage.get(record.object_key)
    assert hashlib.sha256(gz).hexdigest() == record.checksum
    assert record.size_bytes == len(gz)

    # ---- Archive content: ONLY (t, p1) -------------------------------------
    manifest, arango, minio = parse_archive(gz)
    assert manifest.tenant_id == _T and manifest.project_id == "p1"

    msgs = {r["_key"] for r in arango["c3_messages"]}
    assert msgs == {"m1", "m2"}  # p2 + other-tenant excluded
    assert {r["_key"] for r in arango["memory_sources"]} == {"s1"}

    mem = minio["memory"]
    assert set(mem) == {f"sources/{_T}/p1/s1/doc.md", f"sources/{_T}/p1/s1/raw.bin"}
    assert mem[f"sources/{_T}/p1/s1/doc.md"] == b"# keep"

    # ---- Manifest is a complete inventory (absent stores recorded, count 0) -
    names = {(e.store, e.name) for e in manifest.entries}
    assert (Store.ARANGO, "c3_messages") in names
    assert (Store.MINIO, "requirements") in names  # bucket absent → count 0
    absent = next(e for e in manifest.entries
                  if e.store is Store.MINIO and e.name == "requirements")
    assert absent.count == 0
