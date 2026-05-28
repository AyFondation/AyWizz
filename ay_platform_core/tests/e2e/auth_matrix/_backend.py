# =============================================================================
# File: _backend.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/_backend.py
# Description: Direct ArangoDB / MinIO assertion helpers for the
#              backend-state tests. These bypass the HTTP API and
#              query the persistence layer directly so the test
#              proves not just "the API returned 2xx" but "the data
#              ACTUALLY hit the store".
#
#              Helpers are sync (python-arango + minio are sync
#              drivers); tests call them via `await asyncio.to_thread(...)`
#              when running inside async tests.
# =============================================================================

from __future__ import annotations

from typing import Any

from minio import Minio
from minio.error import S3Error

# ---------------------------------------------------------------------------
# ArangoDB
# ---------------------------------------------------------------------------


def assert_arango_doc_exists(
    db: Any, collection: str, key: str
) -> dict[str, Any]:
    """Assert that document `_key=key` exists in `collection`. Returns the doc."""
    coll = db.collection(collection)
    doc = coll.get(key)
    assert doc is not None, (
        f"document `{key}` not found in arango collection `{collection}` — "
        f"the API call returned 2xx but the row was never persisted."
    )
    return doc  # type: ignore[no-any-return]


def assert_arango_doc_absent(
    db: Any, collection: str, key: str
) -> None:
    """Assert that document `_key=key` does NOT exist in `collection`."""
    coll = db.collection(collection)
    doc = coll.get(key)
    assert doc is None, (
        f"document `{key}` still present in arango collection `{collection}` — "
        f"the DELETE call returned 2xx but the row was not removed. "
        f"Doc: {doc}"
    )


def count_arango_docs(db: Any, collection: str, **filters: Any) -> int:
    """Count documents in `collection` matching every key=value filter.
    Used to assert "tenant_a's documents are not visible to tenant_b
    via direct DB query"."""
    coll = db.collection(collection)
    if not filters:
        return int(coll.count())
    aql = (
        f"FOR d IN {collection} "
        + " ".join(f"FILTER d.{k} == @{k}" for k in filters)
        + " COLLECT WITH COUNT INTO n RETURN n"
    )
    cursor = db.aql.execute(aql, bind_vars=filters)
    rows = list(cursor)
    return int(rows[0]) if rows else 0


# ---------------------------------------------------------------------------
# MinIO
# ---------------------------------------------------------------------------


def assert_minio_object_exists(client: Minio, bucket: str, key: str) -> dict[str, Any]:
    """Assert that object `key` exists in `bucket`. Returns its stat."""
    try:
        stat = client.stat_object(bucket, key)
    except S3Error as exc:
        raise AssertionError(
            f"object `{key}` not found in minio bucket `{bucket}` — "
            f"the API call returned 2xx but the object was never written. "
            f"S3 error: {exc.code}"
        ) from exc
    return {
        "size": stat.size,
        "etag": stat.etag,
        "content_type": stat.content_type,
    }


def assert_minio_object_absent(client: Minio, bucket: str, key: str) -> None:
    """Assert that object `key` does NOT exist in `bucket`."""
    try:
        client.stat_object(bucket, key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return
        raise AssertionError(
            f"unexpected S3 error checking absence of `{key}` in `{bucket}`: "
            f"{exc.code}"
        ) from exc
    raise AssertionError(
        f"object `{key}` still present in minio bucket `{bucket}` — "
        f"the DELETE call returned 2xx but the object was not removed."
    )


__all__ = [
    "assert_arango_doc_absent",
    "assert_arango_doc_exists",
    "assert_minio_object_absent",
    "assert_minio_object_exists",
    "count_arango_docs",
]
