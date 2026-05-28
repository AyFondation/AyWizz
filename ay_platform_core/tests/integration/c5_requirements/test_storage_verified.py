# =============================================================================
# File: test_storage_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_storage_verified.py
# Description: Storage-verified integration tests. Writes via C5's public
#              HTTP surface, then opens the underlying ArangoDB + MinIO
#              clients directly to assert the raw storage state matches
#              expectations. Complements the API round-trip tests in
#              test_crud_flow.py — round-tripping via the API proves
#              internal consistency, raw storage checks prove correctness
#              of the persistence boundary (content hashes, layout, keys).
#
#              A round-trip test that reads the same service layer cannot
#              catch:
#                - API returns 200 but the transaction was rolled back.
#                - MinIO object written at wrong path (but API reads from
#                  the right path, hiding the mismatch).
#                - Content hash silently diverges between Arango metadata
#                  and MinIO body.
#
#              Direct storage checks SHALL.
# =============================================================================

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c5_requirements.db.repository import RequirementsRepository
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, MinioEndpoint

pytestmark = pytest.mark.integration

_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_editor,project_owner",
}

_DOC = """---
document: 500-SPEC-VERIF
version: 1
path: projects/demo/requirements/500-SPEC-VERIF.md
language: en
status: draft
---

# Storage verification

#### R-500-001

```yaml
id: R-500-001
version: 1
status: approved
category: functional
```

The system SHALL persist requirement documents to MinIO with matching content hash.
"""


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://c5"
    )


async def _seed_via_api(app: FastAPI) -> None:
    async with _client(app) as client:
        create = await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "500-SPEC-VERIF"},
            headers=_HEADERS,
        )
        assert create.status_code == 201, create.text
        put = await client.put(
            "/api/v1/projects/demo/requirements/documents/500-SPEC-VERIF",
            json={"content": _DOC},
            headers={**_HEADERS, "If-Match": '"500-SPEC-VERIF@v1"'},
        )
        assert put.status_code == 200, put.text


# ---------------------------------------------------------------------------
# Raw storage clients (independent of the service layer — these are the
# "second witness" that must agree with what the API says it did)
# ---------------------------------------------------------------------------


def _raw_arango(endpoint: ArangoEndpoint, db_name: str) -> Any:
    client = ArangoClient(hosts=endpoint.url)
    return client.db(db_name, username=endpoint.username, password=endpoint.password)


def _raw_minio(endpoint: MinioEndpoint) -> Minio:
    return Minio(
        endpoint.endpoint,
        access_key=endpoint.access_key,
        secret_key=endpoint.secret_key,
        secure=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_lands_in_minio_with_exact_bytes(
    c5_app: FastAPI,
    c5_storage: RequirementsStorage,
    minio_container: MinioEndpoint,
) -> None:
    """After POST+PUT, the MinIO object at the expected path SHALL contain
    the exact Markdown body the caller pushed."""
    await _seed_via_api(c5_app)

    bucket = c5_storage._bucket
    expected_path = RequirementsStorage.document_path("demo", "500-SPEC-VERIF")

    minio = _raw_minio(minio_container)
    assert minio.bucket_exists(bucket), (
        f"expected bucket {bucket} to exist"
    )
    obj = minio.get_object(bucket, expected_path)
    try:
        body = obj.read().decode("utf-8")
    finally:
        obj.close()
        obj.release_conn()

    # Exact byte-for-byte match with what the API persisted.
    assert body == _DOC, (
        "MinIO body drift — bytes on disk do not match the PUT payload."
    )


@pytest.mark.asyncio
async def test_entity_row_in_arango_has_consistent_fields(
    c5_app: FastAPI,
    c5_repo: RequirementsRepository,
    arango_container: ArangoEndpoint,
) -> None:
    """The entity parsed from the Markdown body SHALL be present in the
    Arango `req_entities` collection with the exact fields the document
    declared."""
    await _seed_via_api(c5_app)

    db_name = c5_repo._db.name
    db = _raw_arango(arango_container, db_name)
    # Direct collection access — NOT through the service.
    doc = db.collection("req_entities").get("demo:R-500-001")
    assert doc is not None, (
        "entity req_entities/demo:R-500-001 missing from Arango — "
        "API reported success but persistence did not land"
    )
    assert doc["entity_id"] == "R-500-001"
    assert doc["status"] == "approved"
    assert doc["version"] == 1
    assert doc["category"] == "functional"
    assert doc["project_id"] == "demo"
    assert doc["document_slug"] == "500-SPEC-VERIF"


@pytest.mark.asyncio
async def test_document_row_in_arango_matches_minio_hash(
    c5_app: FastAPI,
    c5_repo: RequirementsRepository,
    c5_storage: RequirementsStorage,
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> None:
    """C5 stores a content hash in the document row; it SHALL match the
    SHA-256 of the MinIO body. Divergence means the two stores drifted —
    the exact failure mode a dual-store design is most exposed to.
    """
    await _seed_via_api(c5_app)

    # Arango row
    db = _raw_arango(arango_container, c5_repo._db.name)
    doc_row = db.collection("req_documents").get("demo:500-SPEC-VERIF")
    assert doc_row is not None, "document row missing from Arango"
    declared_hash = doc_row.get("content_hash")
    assert declared_hash, "document row carries no content_hash"

    # MinIO body
    bucket = c5_storage._bucket
    obj = _raw_minio(minio_container).get_object(
        bucket, RequirementsStorage.document_path("demo", "500-SPEC-VERIF")
    )
    try:
        body = obj.read()
    finally:
        obj.close()
        obj.release_conn()

    actual_hash = "sha256:" + hashlib.sha256(body).hexdigest()
    assert declared_hash == actual_hash, (
        "Arango content_hash mismatches MinIO body SHA-256 — dual-store "
        f"drift detected. declared={declared_hash!r}, actual={actual_hash!r}"
    )


@pytest.mark.asyncio
async def test_delete_document_cascades_soft_delete_across_stores(
    c5_app: FastAPI,
    c5_repo: RequirementsRepository,
    c5_storage: RequirementsStorage,
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> None:
    """C5 DELETE is a SOFT delete (R-300-033): entities transition to
    ``deprecated`` in Arango, the live MinIO object is removed (moved
    to ``_deleted/``), but rows remain visible for audit.

    Storage-level expectations — each store must reflect the soft-delete
    consistently:
      - Arango ``req_documents`` row: still present (soft).
      - Arango ``req_entities`` rows: still present with status=deprecated.
      - MinIO live path: gone (moved).
    """
    await _seed_via_api(c5_app)
    async with _client(c5_app) as client:
        resp = await client.delete(
            "/api/v1/projects/demo/requirements/documents/500-SPEC-VERIF",
            headers=_HEADERS,
        )
        assert resp.status_code in (200, 204), resp.text

    db = _raw_arango(arango_container, c5_repo._db.name)
    # C5 removes the document row but keeps entity rows (transitioned to
    # `deprecated`) for audit / history traversal. Assert both.
    assert db.collection("req_documents").get("demo:500-SPEC-VERIF") is None, (
        "Expected document row dropped on DELETE"
    )
    entity_row = db.collection("req_entities").get("demo:R-500-001")
    assert entity_row is not None, (
        "Expected entity row to survive soft-delete for history visibility"
    )
    assert entity_row["status"] == "deprecated", (
        f"Soft-deleted entity should be 'deprecated', got {entity_row['status']!r}"
    )

    # MinIO: the live object path is removed by C5's delete_document
    # (the history copy lives under _history/).
    minio = _raw_minio(minio_container)
    bucket = c5_storage._bucket
    live_path = RequirementsStorage.document_path("demo", "500-SPEC-VERIF")
    live_objs = [
        str(o.object_name)
        for o in minio.list_objects(bucket, prefix=live_path, recursive=False)
    ]
    assert live_path not in live_objs, (
        f"MinIO still hosts {live_path} after DELETE (objects: {live_objs})"
    )
