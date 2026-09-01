# =============================================================================
# File: test_backup_api.py
# Version: 1
# Path: ay_platform_core/tests/integration/c16_backup/test_backup_api.py
# Description: Integration tests for the C16 HTTP surface (900-SPEC §4 /
#              R-900-005..008/011) against a real ArangoDB + MinIO via ASGI:
#              snapshot -> list -> download -> restore (dry-run) -> upload,
#              plus the authorisation hierarchy (anonymous 401, no-role 403,
#              cross-tenant isolation 404, platform_manager all-access).
#
# @relation validates:R-900-005
# @relation validates:R-900-006
# @relation validates:R-900-007
# @relation validates:R-900-008
# @relation validates:R-900-011
# =============================================================================

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

from ay_platform_core.c16_backup.repository import BackupRecordRepository, BackupStorage
from ay_platform_core.c16_backup.router import router
from ay_platform_core.c16_backup.service import BackupService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_T = "tenant-api"
_P = "proj-api"
_OWNER = {"X-User-Id": "alice", "X-Tenant-Id": _T, "X-User-Roles": "project_owner"}
_NOROLE = {"X-User-Id": "u", "X-Tenant-Id": _T, "X-User-Roles": "project_viewer"}
_PM = {"X-User-Id": "root", "X-Tenant-Id": "other", "X-User-Roles": "platform_manager"}
_OTHER = {"X-User-Id": "bob", "X-Tenant-Id": "tenant-z", "X-User-Roles": "project_owner"}
_TADMIN = {"X-User-Id": "ta", "X-Tenant-Id": _T, "X-User-Roles": "tenant_admin"}


@pytest_asyncio.fixture(scope="function")
async def app(
    arango_container: ArangoEndpoint, minio_container: MinioEndpoint,
) -> AsyncIterator[FastAPI]:
    db_name = f"c16api_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    db.create_collection("c3_messages")
    db.collection("c3_messages").insert(
        {"_key": "m1", "tenant_id": _T, "project_id": _P, "body": "hi"})

    client = Minio(
        minio_container.endpoint, access_key=minio_container.access_key,
        secret_key=minio_container.secret_key, secure=False,
    )
    bucket = f"backups-{uuid.uuid4().hex[:8]}"
    records = BackupRecordRepository(db)
    records.ensure_collections()
    service = BackupService(
        db=db, minio=client, storage=BackupStorage(client, bucket), records=records,
    )
    fastapi = FastAPI()
    fastapi.include_router(router)
    fastapi.state.backup_service = service
    try:
        yield fastapi
    finally:
        cleanup_minio_bucket(minio_container, bucket)
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://c16")


async def test_snapshot_list_download_restore_upload(app: FastAPI) -> None:
    async with _client(app) as c:
        # ---- snapshot (project_owner) --------------------------------------
        r = await c.post(f"/api/v1/projects/{_P}/backups", headers=_OWNER)
        assert r.status_code == 201, r.text
        rec = r.json()
        backup_id = rec["backup_id"]
        assert rec["origin"] == "generated"

        # ---- list ----------------------------------------------------------
        r = await c.get(f"/api/v1/projects/{_P}/backups", headers=_OWNER)
        assert r.status_code == 200
        assert backup_id in {b["backup_id"] for b in r.json()}

        # ---- download (checksum matches the record) ------------------------
        r = await c.get(
            f"/api/v1/projects/{_P}/backups/{backup_id}/download", headers=_OWNER)
        assert r.status_code == 200
        gz = r.content
        assert hashlib.sha256(gz).hexdigest() == rec["checksum"]

        # ---- restore (dry-run) → report, nothing written -------------------
        r = await c.post(
            f"/api/v1/projects/{_P}/backups/{backup_id}/restore",
            headers=_OWNER, json={"dry_run": True})
        assert r.status_code == 200, r.text
        assert r.json()["dry_run"] is True and r.json()["committed"] is False

        # ---- upload the downloaded archive → new UPLOADED record -----------
        r = await c.post(
            f"/api/v1/projects/{_P}/backups/archives", headers=_OWNER,
            files={"file": ("b.tar.gz", io.BytesIO(gz), "application/gzip")})
        assert r.status_code == 201, r.text
        assert r.json()["origin"] == "uploaded"


async def test_authorization_and_isolation(app: FastAPI) -> None:
    async with _client(app) as c:
        # A backup owned by (_T, _P).
        rec = (await c.post(f"/api/v1/projects/{_P}/backups", headers=_OWNER)).json()
        bid = rec["backup_id"]
        dl = f"/api/v1/projects/{_P}/backups/{bid}/download"

        # Anonymous → 401.
        assert (await c.post(f"/api/v1/projects/{_P}/backups")).status_code == 401
        # Authenticated but no accepted role → 403.
        assert (await c.get(
            f"/api/v1/projects/{_P}/backups", headers=_NOROLE)).status_code == 403
        # Cross-tenant owner → 404 (isolation: no existence leak).
        assert (await c.get(dl, headers=_OTHER)).status_code == 404
        # platform_manager (different tenant) → full access.
        assert (await c.get(dl, headers=_PM)).status_code == 200


async def test_tenant_scope_endpoints(app: FastAPI) -> None:
    async with _client(app) as c:
        # tenant_admin snapshots the WHOLE tenant.
        r = await c.post(f"/api/v1/tenants/{_T}/backups", headers=_TADMIN)
        assert r.status_code == 201, r.text
        rec = r.json()
        bid = rec["backup_id"]
        assert rec["scope"] == "tenant" and rec["project_id"] is None

        # list (tenant scope).
        r = await c.get(f"/api/v1/tenants/{_T}/backups", headers=_TADMIN)
        assert r.status_code == 200 and bid in {b["backup_id"] for b in r.json()}

        # download → gz.
        dl = f"/api/v1/tenants/{_T}/backups/{bid}/download"
        r = await c.get(dl, headers=_TADMIN)
        assert r.status_code == 200
        gz = r.content

        # restore tenant-as-new (dry-run).
        r = await c.post(
            f"/api/v1/tenants/{_T}/backups/{bid}/restore",
            headers=_TADMIN, json={"dry_run": True})
        assert r.status_code == 200 and r.json()["dry_run"] is True

        # upload under the tenant.
        r = await c.post(
            f"/api/v1/tenants/{_T}/backups/archives", headers=_TADMIN,
            files={"file": ("t.tar.gz", io.BytesIO(gz), "application/gzip")})
        assert r.status_code == 201

        # project_owner is NOT sufficient for tenant scope → 403.
        assert (await c.post(
            f"/api/v1/tenants/{_T}/backups", headers=_OWNER)).status_code == 403
        # tenant_admin of ANOTHER tenant → 404 (isolation, no existence leak).
        other_ta = {"X-User-Id": "x", "X-Tenant-Id": "tz", "X-User-Roles": "tenant_admin"}
        assert (await c.get(
            f"/api/v1/tenants/{_T}/backups", headers=other_ta)).status_code == 404
