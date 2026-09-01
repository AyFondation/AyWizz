# =============================================================================
# File: archive.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c16_backup/archive.py
# Description: Pure archive (de)serialisation for C16 (R-900-003 / R-900-013).
#              Builds a self-describing `.tar.gz` — `manifest.json` at the root,
#              `arango/<collection>.jsonl` (one JSON doc per line),
#              `minio/<bucket>/<key>` (raw bytes) — with a per-store sha256 +
#              count in the manifest. `parse_archive` reverses it and VERIFIES
#              every checksum + count (rejects a tampered/truncated archive).
#              No I/O to Arango/MinIO here — this layer is deterministic and
#              fully unit-testable.
#
# @relation implements:R-900-003
# @relation implements:R-900-013
# =============================================================================

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from typing import Any

from ay_platform_core.c16_backup.data_map import MANIFEST_VERSION
from ay_platform_core.c16_backup.models import (
    BackupManifest,
    BackupScope,
    ManifestEntry,
    Store,
)

# Type aliases for the two slice shapes an archive carries.
ArangoSlices = dict[str, list[dict[str, Any]]]  # collection -> rows
MinioSlices = dict[str, dict[str, bytes]]  # bucket -> {key: bytes}


class ArchiveIntegrityError(Exception):
    """A checksum or count in the manifest does not match the payload."""


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    """Deterministic JSON-lines encoding: sorted keys, one row per line."""
    return "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for r in rows
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _minio_digest(objects: dict[str, bytes]) -> str:
    """Order-independent digest of a bucket slice: hash sorted
    key + length + bytes, so the checksum is stable regardless of listing
    order."""
    h = hashlib.sha256()
    for key in sorted(objects):
        blob = objects[key]
        h.update(key.encode("utf-8"))
        h.update(b"\0")
        h.update(str(len(blob)).encode("ascii"))
        h.update(b"\0")
        h.update(blob)
        h.update(b"\0")
    return h.hexdigest()


def build_archive(
    *,
    scope: BackupScope,
    tenant_id: str,
    project_id: str | None,
    arango: ArangoSlices,
    minio: MinioSlices,
    platform_version: str = "",
    created_at: str | None = None,
) -> tuple[bytes, BackupManifest]:
    """Serialise the given slices into a `.tar.gz` + its manifest. Returns
    (archive_bytes, manifest). Empty collections/buckets are still recorded
    (count 0) so the manifest is a complete inventory."""
    now = created_at or datetime.now(UTC).isoformat()
    entries: list[ManifestEntry] = []

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        # Arango members.
        for coll in sorted(arango):
            rows = arango[coll]
            payload = _jsonl(rows)
            entries.append(ManifestEntry(
                store=Store.ARANGO, name=coll, count=len(rows),
                sha256=_sha256(payload),
            ))
            _add(tar, f"arango/{coll}.jsonl", payload)
        # MinIO members.
        for bucket in sorted(minio):
            objects = minio[bucket]
            entries.append(ManifestEntry(
                store=Store.MINIO, name=bucket, count=len(objects),
                sha256=_minio_digest(objects),
            ))
            for key in sorted(objects):
                _add(tar, f"minio/{bucket}/{key}", objects[key])
        # Manifest LAST so it can carry every checksum.
        manifest = BackupManifest(
            manifest_version=MANIFEST_VERSION, scope=scope, tenant_id=tenant_id,
            project_id=project_id, created_at=now,
            platform_version=platform_version, entries=entries,
        )
        _add(tar, "manifest.json",
             manifest.model_dump_json(indent=2).encode("utf-8"))

    gz = gzip.compress(raw.getvalue())
    return gz, manifest


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0  # deterministic
    tar.addfile(info, io.BytesIO(data))


def parse_archive(gz: bytes) -> tuple[BackupManifest, ArangoSlices, MinioSlices]:
    """Reverse `build_archive`, VERIFYING every manifest checksum + count.
    Raises `ArchiveIntegrityError` on any mismatch or a malformed archive."""
    try:
        tar_bytes = gzip.decompress(gz)
    except (OSError, EOFError) as exc:
        raise ArchiveIntegrityError(f"not a valid gzip archive: {exc}") from exc

    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tar:
            for info in tar.getmembers():
                if not info.isfile():
                    continue
                f = tar.extractfile(info)
                members[info.name] = f.read() if f is not None else b""
    except tarfile.TarError as exc:
        raise ArchiveIntegrityError(f"corrupt tar payload: {exc}") from exc

    if "manifest.json" not in members:
        raise ArchiveIntegrityError("archive has no manifest.json")
    try:
        manifest = BackupManifest.model_validate_json(members["manifest.json"])
    except ValueError as exc:
        raise ArchiveIntegrityError(f"invalid manifest.json: {exc}") from exc

    arango: ArangoSlices = {}
    minio: MinioSlices = {}
    for entry in manifest.entries:
        if entry.store is Store.ARANGO:
            arango[entry.name] = _read_arango_entry(members, entry)
        else:
            minio[entry.name] = _read_minio_entry(members, entry)

    return manifest, arango, minio


def _verify(ok: bool, kind: str, name: str) -> None:
    if not ok:
        raise ArchiveIntegrityError(f"{kind} mismatch for {name}")


def _read_arango_entry(
    members: dict[str, bytes], entry: ManifestEntry,
) -> list[dict[str, Any]]:
    payload = members.get(f"arango/{entry.name}.jsonl", b"")
    _verify(_sha256(payload) == entry.sha256, "checksum", f"arango/{entry.name}")
    rows = [json.loads(line) for line in payload.splitlines() if line]
    _verify(len(rows) == entry.count, "count", f"arango/{entry.name}")
    return rows


def _read_minio_entry(
    members: dict[str, bytes], entry: ManifestEntry,
) -> dict[str, bytes]:
    prefix = f"minio/{entry.name}/"
    objects = {
        name[len(prefix):]: blob
        for name, blob in members.items()
        if name.startswith(prefix)
    }
    _verify(_minio_digest(objects) == entry.sha256, "checksum", f"minio/{entry.name}")
    _verify(len(objects) == entry.count, "count", f"minio/{entry.name}")
    return objects
