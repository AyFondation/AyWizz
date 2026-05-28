# =============================================================================
# File: test_storage_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c6_validation/test_storage_verified.py
# Description: Storage-verified integration tests for C6. Triggers a run
#              via the service, then reads the raw ArangoDB + MinIO state
#              directly to assert:
#                - `c6_runs` row present with status=completed, counts
#                  matching the actual findings count, snapshot_uri set.
#                - `c6_findings` rows count matches the run's
#                  findings_count aggregate.
#                - MinIO snapshot object exists at the declared path with
#                  a well-formed JSON body.
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from minio import Minio

from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    RunStatus,
    RunTriggerRequest,
)
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)
from tests.fixtures.containers import ArangoEndpoint, MinioEndpoint

pytestmark = pytest.mark.integration


def _raw_arango(endpoint: ArangoEndpoint, db_name: str) -> Any:
    return ArangoClient(hosts=endpoint.url).db(
        db_name, username=endpoint.username, password=endpoint.password
    )


def _raw_minio(endpoint: MinioEndpoint) -> Minio:
    return Minio(
        endpoint.endpoint,
        access_key=endpoint.access_key,
        secret_key=endpoint.secret_key,
        secure=False,
    )


@pytest.mark.asyncio
async def test_completed_run_lands_in_arango_and_minio(
    c6_service: ValidationService,
    c6_repo: Any,
    c6_snapshot_store: ValidationSnapshotStorage,
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> None:
    """Trigger a run that produces one blocking finding; verify both
    stores have consistent state post-completion."""
    # Use a real check with a known violation → deterministic finding count.
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["code-without-requirement"],
    )
    artifacts = [
        CodeArtifact(path="src/empty.py", content="def x(): pass\n"),
    ]
    run = await c6_service.execute_run_sync(
        payload, requirements=[], artifacts=artifacts
    )
    assert run.status == RunStatus.COMPLETED

    # --- Arango: c6_runs row ---
    db = _raw_arango(arango_container, c6_repo._db.name)
    runs_coll = db.collection("c6_runs")
    row = runs_coll.get(run.run_id)
    assert row is not None, "run row missing from c6_runs"
    assert row["status"] == "completed"
    assert row["project_id"] == "demo"
    assert row["domain"] == "code"
    assert row["findings_count"]["blocking"] == 1, (
        f"blocking count drift: row says "
        f"{row['findings_count']['blocking']}, run says "
        f"{run.findings_count.blocking}"
    )
    assert row["snapshot_uri"] is not None

    # --- Arango: c6_findings rows match the aggregate ---
    findings_coll = db.collection("c6_findings")
    cursor = db.aql.execute(
        "FOR f IN c6_findings FILTER f.run_id == @rid RETURN f",
        bind_vars={"rid": run.run_id},
    )
    findings = list(cursor)
    total = (
        run.findings_count.blocking
        + run.findings_count.advisory
        + run.findings_count.info
    )
    assert len(findings) == total, (
        f"c6_findings count {len(findings)} disagrees with run aggregate {total}"
    )
    check_ids = {f["check_id"] for f in findings}
    assert "code-without-requirement" in check_ids

    # --- MinIO: snapshot JSON exists and is well-formed ---
    minio = _raw_minio(minio_container)
    bucket = c6_snapshot_store._bucket
    obj = minio.get_object(bucket, str(row["snapshot_uri"]))
    try:
        body = obj.read()
    finally:
        obj.close()
        obj.release_conn()
    snapshot = json.loads(body)
    assert snapshot["run"]["run_id"] == run.run_id
    assert snapshot["run"]["status"] == "completed"
    # The snapshot findings list SHALL match the Arango content.
    assert len(snapshot["findings"]) == total
    _ = findings_coll  # silence unused (kept for future assertions)


@pytest.mark.asyncio
async def test_empty_run_still_writes_snapshot(
    c6_service: ValidationService,
    c6_repo: Any,
    c6_snapshot_store: ValidationSnapshotStorage,
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> None:
    """A run that produces no findings (stub-only check, no artifacts) SHALL
    still persist a run row and a snapshot to MinIO — the snapshot is an
    immutable audit trail, not a cache of findings."""
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["interface-signature-drift"],  # stub → 1 info finding
    )
    run = await c6_service.execute_run_sync(
        payload, requirements=[], artifacts=[]
    )

    db = _raw_arango(arango_container, c6_repo._db.name)
    row = db.collection("c6_runs").get(run.run_id)
    assert row is not None
    assert row["status"] == "completed"

    minio = _raw_minio(minio_container)
    bucket = c6_snapshot_store._bucket
    assert minio.bucket_exists(bucket)
    # Listing objects at the expected prefix SHALL include this run.
    prefix = f"validation-reports/{run.project_id}/"
    keys = [str(o.object_name) for o in minio.list_objects(bucket, prefix=prefix)]
    assert any(run.run_id in k for k in keys), (
        f"Snapshot for run {run.run_id} not found under {prefix} (found: {keys})"
    )
