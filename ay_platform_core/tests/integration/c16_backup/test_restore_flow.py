# =============================================================================
# File: test_restore_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c16_backup/test_restore_flow.py
# Description: The C16 round-trip INVARIANT test (R-900-008/009) against a REAL
#              ArangoDB + MinIO: seed a project spanning an id-keyed row, opaque
#              rows, a KG entity/relation EDGE graph, and a MinIO object;
#              snapshot it; restore-as-new into a fresh project; then assert the
#              restored project is DEEP-EQUAL to the source MODULO the remapped
#              ids — including that the restored edge still connects the same
#              entities (referential integrity), and a dry-run writes nothing.
#
# @relation validates:R-900-008
# @relation validates:R-900-009
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

from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage
from ay_platform_core.c16_backup.service import BackupService

pytestmark = pytest.mark.integration

_T = "tenant-r"
_P = "proj-src"


@pytest.fixture(scope="function")
def stack(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint,
) -> Iterator[dict[str, Any]]:
    db_name = f"c16r_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    db.create_collection("c2_projects")
    db.create_collection("c3_messages")
    db.create_collection("memory_kg_entities")
    db.create_collection("memory_kg_relations", edge=True)
    db.collection("c2_projects").insert(
        {"_key": _P, "tenant_id": _T, "project_id": _P, "name": "Proj"})
    db.collection("c3_messages").insert(
        {"_key": "m1", "tenant_id": _T, "project_id": _P, "body": "hello"})
    db.collection("memory_kg_entities").insert_many([
        {"_key": "ent-1", "tenant_id": _T, "project_id": _P, "name": "Foo"},
        {"_key": "ent-2", "tenant_id": _T, "project_id": _P, "name": "Bar"},
    ])
    db.collection("memory_kg_relations").insert({
        "_key": "rel-1", "tenant_id": _T, "project_id": _P,
        "_from": "memory_kg_entities/ent-1", "_to": "memory_kg_entities/ent-2",
        "relation": "USES",
    })

    client = Minio(
        minio_container.endpoint, access_key=minio_container.access_key,
        secret_key=minio_container.secret_key, secure=False,
    )
    client.make_bucket("memory")
    client.put_object("memory", f"sources/{_T}/{_P}/s1/doc.md",
                      io.BytesIO(b"# doc"), length=5)

    backups_bucket = f"backups-{uuid.uuid4().hex[:8]}"
    records = BackupRecordRepository(db)
    records.ensure_collections()
    service = BackupService(
        db=db, minio=client, storage=BackupStorage(client, backups_bucket),
        records=records,
    )
    try:
        yield {"db": db, "client": client, "service": service,
               "backups_bucket": backups_bucket}
    finally:
        cleanup_minio_bucket(minio_container, "memory")
        cleanup_minio_bucket(minio_container, backups_bucket)
        cleanup_arango_database(arango_container, db_name)


def _rows(db: Any, coll: str, project_id: str) -> list[dict[str, Any]]:
    return list(db.aql.execute(
        f"FOR d IN {coll} FILTER d.project_id == @p RETURN d",
        bind_vars={"p": project_id},
    ))


def test_restore_as_new_roundtrip(stack: dict[str, Any]) -> None:
    db = stack["db"]
    client: Minio = stack["client"]
    service: BackupService = stack["service"]

    record = service.create_project_snapshot(
        tenant_id=_T, project_id=_P, created_by="alice")
    gz = BackupStorage(client, stack["backups_bucket"]).get(record.object_key)

    # ---- Dry-run writes NOTHING --------------------------------------------
    dry = service.restore_project_as_new(
        gz, target_tenant_id=_T, dry_run=True, new_project_id="proj-dry")
    assert dry.dry_run and not dry.committed
    assert _rows(db, "c3_messages", "proj-dry") == []  # nothing written

    # ---- Real restore into a fresh project ---------------------------------
    new_pid = "proj-restored"
    report = service.restore_project_as_new(
        gz, target_tenant_id=_T, dry_run=False, new_project_id=new_pid)
    assert report.committed and report.new_project_id == new_pid

    # PROJECT_ID-keyed row: _key IS the new project id.
    projs = _rows(db, "c2_projects", new_pid)
    assert len(projs) == 1 and projs[0]["_key"] == new_pid
    assert projs[0]["name"] == "Proj" and projs[0]["tenant_id"] == _T

    # Opaque row: content preserved, id remapped.
    msgs = _rows(db, "c3_messages", new_pid)
    assert len(msgs) == 1 and msgs[0]["body"] == "hello"
    assert msgs[0]["_key"] != "m1"

    # KG graph: same two entities, and the edge still connects Foo -> Bar
    # (referential integrity across the remap).
    ents = {e["_key"]: e["name"] for e in _rows(db, "memory_kg_entities", new_pid)}
    assert set(ents.values()) == {"Foo", "Bar"}
    rels = _rows(db, "memory_kg_relations", new_pid)
    assert len(rels) == 1
    frm = rels[0]["_from"].split("/", 1)[1]
    to = rels[0]["_to"].split("/", 1)[1]
    assert ents[frm] == "Foo" and ents[to] == "Bar"  # edge preserved

    # MinIO object restored under the new project prefix, content identical.
    resp = client.get_object("memory", f"sources/{_T}/{new_pid}/s1/doc.md")
    try:
        assert resp.read() == b"# doc"
    finally:
        resp.close()
        resp.release_conn()

    # Source project is untouched (restore-as-new never overwrites).
    assert len(_rows(db, "c3_messages", _P)) == 1
