# =============================================================================
# File: remap.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/remap.py
# Description: The restore-as-new id remapper (R-900-008). Given parsed archive
#              slices for a SOURCE (tenant, project), produce slices rehydrated
#              for a NEW (tenant, project): every `_key` is remapped per the
#              collection's KeyStrategy, `tenant_id`/`project_id` fields are
#              rewritten, edge `_from`/`_to` are RE-WIRED through the old->new
#              key map (referential integrity preserved), and MinIO object keys
#              are rewritten. Pure + deterministic given the key factory.
#
# @relation implements:R-900-008
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from ay_platform_core.c16_backup.archive import ArangoSlices, MinioSlices
from ay_platform_core.c16_backup.models import DataMapEntry, KeyStrategy


def _sub(value: str, repl: dict[str, str]) -> str:
    """Substring-replace the source ids with the target ids (longest first so a
    shorter id that is a substring of a longer one can't partially match)."""
    for old in sorted(repl, key=len, reverse=True):
        value = value.replace(old, repl[old])
    return value


def _new_key(
    old_key: str, entry: DataMapEntry, *,
    id_map: dict[str, str], key_factory: Callable[[], str],
) -> str:
    match entry.key_strategy:
        case KeyStrategy.PROJECT_ID | KeyStrategy.TENANT_ID:
            # The key IS an id (project/tenant) — map it to its new value.
            return id_map.get(old_key, old_key)
        case KeyStrategy.COMPOSITE:
            return _sub(old_key, id_map)
        case _:  # OPAQUE — mint a fresh key so it can never collide.
            return key_factory()


def remap_slices(
    arango: ArangoSlices,
    minio: MinioSlices,
    *,
    id_map: dict[str, str],
    entries: dict[str, DataMapEntry],
    key_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> tuple[ArangoSlices, MinioSlices]:
    """Remap every row/object for a new scope. `id_map` maps EVERY source id
    (the tenant, and — for a tenant restore — each project) to its new value.
    `_key` is remapped per KeyStrategy, `tenant_id`/`project_id` fields are
    mapped through `id_map`, edge `_from`/`_to` are rewired via the old->new
    map, and MinIO keys are substring-mapped. Referential integrity holds even
    for hash (OPAQUE) keys."""
    # ---- Pass 1: compute every new _key, build the old->new REF map. --------
    ref_map: dict[str, str] = {}
    new_keys: dict[str, list[str]] = {}
    for coll, rows in arango.items():
        entry = entries[coll]
        keys: list[str] = []
        for row in rows:
            old_key = str(row["_key"])
            nk = _new_key(old_key, entry, id_map=id_map, key_factory=key_factory)
            keys.append(nk)
            ref_map[f"{coll}/{old_key}"] = f"{coll}/{nk}"
        new_keys[coll] = keys

    # ---- Pass 2: rewrite rows (fields + key + rewired edges). ---------------
    out_arango: ArangoSlices = {}
    for coll, rows in arango.items():
        out_rows: list[dict[str, Any]] = []
        for row, nk in zip(rows, new_keys[coll], strict=True):
            new_row = dict(row)
            new_row["_key"] = nk
            for field in ("tenant_id", "project_id"):
                if field in new_row:
                    new_row[field] = id_map.get(str(new_row[field]), new_row[field])
            for ref in ("_from", "_to"):
                if ref in new_row:
                    old_ref = str(new_row[ref])
                    new_row[ref] = ref_map.get(old_ref, _sub(old_ref, id_map))
            out_rows.append(new_row)
        out_arango[coll] = out_rows

    # ---- MinIO: rewrite object keys (path segments carry the ids). ----------
    out_minio: MinioSlices = {
        bucket: {_sub(key, id_map): blob for key, blob in objects.items()}
        for bucket, objects in minio.items()
    }
    return out_arango, out_minio


def remap_project(
    arango: ArangoSlices,
    minio: MinioSlices,
    *,
    source_tenant: str,
    source_project: str,
    target_tenant: str,
    target_project: str,
    entries: dict[str, DataMapEntry],
    key_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> tuple[ArangoSlices, MinioSlices]:
    """Project restore-as-new: thin wrapper over `remap_slices` with the
    two-id map {source_tenant→target_tenant, source_project→target_project}."""
    id_map = {source_project: target_project, source_tenant: target_tenant}
    return remap_slices(
        arango, minio, id_map=id_map, entries=entries, key_factory=key_factory,
    )
