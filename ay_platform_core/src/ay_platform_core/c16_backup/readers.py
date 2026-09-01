# =============================================================================
# File: readers.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/readers.py
# Description: Store-slice readers for C16 snapshots (R-900-002 / R-900-010):
#              read one DataMap entry's rows/objects for a given
#              (tenant_id, project_id), with secret fields BLANKED in place.
#              Project-scope only in this increment (every project-scoped entry
#              carries both tenant_id + project_id, so filtering is uniform).
#
# @relation implements:R-900-002
# @relation implements:R-900-010
# =============================================================================

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from arango.cursor import Cursor

from ay_platform_core.c16_backup.models import DataMapEntry, KeyStrategy

if TYPE_CHECKING:
    from arango.database import StandardDatabase
    from minio import Minio

# Arango system fields dropped from an exported row — they are per-instance and
# must not travel (restore mints fresh ones). `_key` IS kept (identity).
_DROPPED_SYS_FIELDS = ("_id", "_rev")

# Collections keyed by user_id (no tenant_id column) — filtered by the tenant's
# user set at snapshot time rather than a tenant_id predicate.
_USER_KEYED = frozenset({"c2_user_preferences"})


def _clean_row(raw: dict[str, Any], entry: DataMapEntry) -> dict[str, Any]:
    """Drop per-instance sys fields + BLANK secret fields in place (R-900-010)."""
    row = {k: v for k, v in raw.items() if k not in _DROPPED_SYS_FIELDS}
    for field in entry.secret_fields:
        if field in row:
            row[field] = ""
    return row


def read_arango_project_slice(
    db: StandardDatabase,
    entry: DataMapEntry,
    *,
    tenant_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    """Read one project-scoped collection's rows for (tenant_id, project_id),
    dropping `_id`/`_rev` and BLANKING every secret field (R-900-010). Returns
    [] when the collection is absent."""
    if not db.has_collection(entry.name):
        return []
    cursor = cast("Cursor", db.aql.execute(
        f"FOR d IN {entry.name} "
        "FILTER d.tenant_id == @t AND d.project_id == @p RETURN d",
        bind_vars={"t": tenant_id, "p": project_id},
    ))
    return [_clean_row(raw, entry) for raw in cursor]


def read_arango_tenant_slice(
    db: StandardDatabase,
    entry: DataMapEntry,
    *,
    tenant_id: str,
    user_ids: list[str],
) -> list[dict[str, Any]]:
    """Read one collection's rows for a WHOLE tenant (all projects). Rows carry
    `tenant_id`; user-keyed collections (no tenant_id) are filtered by the
    tenant's user set. Secrets blanked, sys fields dropped. [] if absent."""
    if not db.has_collection(entry.name):
        return []
    if entry.key_strategy is KeyStrategy.TENANT_ID:
        # The tenant's OWN row — keyed by the tenant id, no tenant_id column.
        cursor = cast("Cursor", db.aql.execute(
            f"FOR d IN {entry.name} FILTER d._key == @t RETURN d",
            bind_vars={"t": tenant_id},
        ))
    elif entry.name in _USER_KEYED:
        cursor = cast("Cursor", db.aql.execute(
            f"FOR d IN {entry.name} FILTER d._key IN @uids RETURN d",
            bind_vars={"uids": user_ids},
        ))
    else:
        cursor = cast("Cursor", db.aql.execute(
            f"FOR d IN {entry.name} FILTER d.tenant_id == @t RETURN d",
            bind_vars={"t": tenant_id},
        ))
    return [_clean_row(raw, entry) for raw in cursor]


def _key_in_scope(key: str, project_id: str) -> bool:
    """A MinIO object belongs to a project when the project_id appears as a path
    segment of its key — covers every bucket layout
    (`sources/{t}/{p}/...`, `projects/{p}/...`, `validation-reports/{p}/...`,
    `{t}/{p}/...`)."""
    return project_id in key.split("/")


def read_minio_project_slice(
    client: Minio,
    entry: DataMapEntry,
    *,
    tenant_id: str,
    project_id: str,
) -> dict[str, bytes]:
    """Read one bucket's objects for a project (keys containing the project_id
    segment). Returns {} when the bucket is absent."""
    from minio.error import S3Error  # noqa: PLC0415 - optional dep, cold path

    try:
        if not client.bucket_exists(entry.name):
            return {}
        objects = list(client.list_objects(entry.name, recursive=True))
    except S3Error:
        return {}
    out: dict[str, bytes] = {}
    for obj in objects:
        key = cast("str", obj.object_name)
        if not _key_in_scope(key, project_id):
            continue
        resp = client.get_object(entry.name, key)
        try:
            out[key] = resp.read()
        finally:
            resp.close()
            resp.release_conn()
    return out


def read_minio_tenant_slice(
    client: Minio,
    entry: DataMapEntry,
    *,
    tenant_id: str,
    project_ids: list[str],
) -> dict[str, bytes]:
    """Read a bucket's objects for a WHOLE tenant: keys whose segments include
    the tenant_id OR any of the tenant's project ids (buckets vary — some keys
    carry the tenant, some only the project). Returns {} when absent."""
    from minio.error import S3Error  # noqa: PLC0415 - optional dep, cold path

    wanted = {tenant_id, *project_ids}
    try:
        if not client.bucket_exists(entry.name):
            return {}
        objects = list(client.list_objects(entry.name, recursive=True))
    except S3Error:
        return {}
    out: dict[str, bytes] = {}
    for obj in objects:
        key = cast("str", obj.object_name)
        if not wanted.intersection(key.split("/")):
            continue
        resp = client.get_object(entry.name, key)
        try:
            out[key] = resp.read()
        finally:
            resp.close()
            resp.release_conn()
    return out
