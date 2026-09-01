# =============================================================================
# File: test_tenant_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c16_backup/test_tenant_flow.py
# Description: Tenant-scope snapshot + restore-as-new (R-900-002/008) against a
#              real ArangoDB + MinIO: seed a tenant with a tenant-level row and
#              TWO projects (each with data + a MinIO object), snapshot the WHOLE
#              tenant, restore into a FRESH tenant, then assert a new tenant id
#              + one new project id per source project, all project data mapped
#              consistently, and the source tenant untouched.
#
# @relation validates:R-900-002
# @relation validates:R-900-008
# =============================================================================

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from minio import Minio
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

from ay_platform_core.c16_backup.models import BackupScope
from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage
from ay_platform_core.c16_backup.service import BackupService

pytestmark = pytest.mark.integration

_T = "tenant-src"


@pytest.fixture(scope="function")
def stack(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint,
) -> Iterator[dict[str, Any]]:
    db_name = f"c16t_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    db.create_collection("c2_tenants")
    db.create_collection("c2_projects")
    db.create_collection("c3_messages")
    db.collection("c2_tenants").insert({"_key": _T, "name": "Tenant"})
    db.collection("c2_projects").insert_many([
        {"_key": "pa", "tenant_id": _T, "project_id": "pa", "name": "PA"},
        {"_key": "pb", "tenant_id": _T, "project_id": "pb", "name": "PB"},
    ])
    db.collection("c3_messages").insert_many([
        {"_key": "m1", "tenant_id": _T, "project_id": "pa", "body": "hi-a"},
        {"_key": "m2", "tenant_id": _T, "project_id": "pb", "body": "hi-b"},
    ])

    client = Minio(
        minio_container.endpoint, access_key=minio_container.access_key,
        secret_key=minio_container.secret_key, secure=False,
    )
    client.make_bucket("memory")
    client.put_object("memory", f"sources/{_T}/pa/s/a.md", io.BytesIO(b"A"), length=1)
    client.put_object("memory", f"sources/{_T}/pb/s/b.md", io.BytesIO(b"B"), length=1)

    bucket = f"backups-{uuid.uuid4().hex[:8]}"
    records = BackupRecordRepository(db)
    records.ensure_collections()
    service = BackupService(
        db=db, minio=client, storage=BackupStorage(client, bucket), records=records)
    try:
        yield {"db": db, "client": client, "service": service, "bucket": bucket}
    finally:
        cleanup_minio_bucket(minio_container, "memory")
        cleanup_minio_bucket(minio_container, bucket)
        cleanup_arango_database(arango_container, db_name)


def _rows(db: Any, coll: str, field: str, value: str) -> list[dict[str, Any]]:
    return list(db.aql.execute(
        f"FOR d IN {coll} FILTER d.{field} == @v RETURN d", bind_vars={"v": value}))


def test_tenant_snapshot_and_restore_as_new(stack: dict[str, Any]) -> None:
    db = stack["db"]
    client: Minio = stack["client"]
    service: BackupService = stack["service"]

    record = service.create_tenant_snapshot(tenant_id=_T, created_by="root")
    assert record.scope is BackupScope.TENANT and record.project_id is None
    gz = BackupStorage(client, stack["bucket"]).get(record.object_key)

    report = service.restore_tenant_as_new(gz, dry_run=False, new_tenant_id="T2")
    assert report.committed
    assert report.target_tenant_id == "T2"
    assert len(report.new_project_ids) == 2  # one per source project

    # Tenant-level row: TENANT_ID key → new tenant id.
    tenants = _rows(db, "c2_tenants", "name", "Tenant")
    assert "T2" in {t["_key"] for t in tenants}

    # Both projects restored under new ids, bound to the new tenant.
    new_projects = _rows(db, "c2_projects", "tenant_id", "T2")
    assert {p["name"] for p in new_projects} == {"PA", "PB"}
    new_pids = {p["_key"] for p in new_projects}
    assert new_pids == set(report.new_project_ids)

    # Each project's message is under its NEW project id, tenant remapped.
    for p in new_projects:
        msgs = _rows(db, "c3_messages", "project_id", p["_key"])
        assert len(msgs) == 1 and msgs[0]["tenant_id"] == "T2"
        expected = "hi-a" if p["name"] == "PA" else "hi-b"
        assert msgs[0]["body"] == expected

    # MinIO objects restored under the new project prefixes.
    keys = {o.object_name for o in client.list_objects("memory", recursive=True)}
    for np in report.new_project_ids:
        assert any(f"/{np}/" in k and k.startswith("sources/T2/") for k in keys)

    # Source tenant untouched (restore-as-new never overwrites).
    assert len(_rows(db, "c2_projects", "tenant_id", _T)) == 2
