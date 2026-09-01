# =============================================================================
# File: test_archive.py
# Version: 1
# Path: ay_platform_core/tests/unit/c16_backup/test_archive.py
# Description: Unit tests for the pure archive (de)serialisation (R-900-003 /
#              R-900-013): build → parse round-trip preserves rows + objects +
#              manifest; empty slices are recorded; and integrity is enforced —
#              a tampered payload (checksum/count mismatch) or garbage input is
#              rejected.
#
# @relation validates:R-900-003
# @relation validates:R-900-013
# =============================================================================

from __future__ import annotations

import gzip
import io
import tarfile

import pytest

from ay_platform_core.c16_backup.archive import (
    ArchiveIntegrityError,
    build_archive,
    parse_archive,
)
from ay_platform_core.c16_backup.models import BackupScope, Store

pytestmark = pytest.mark.unit

_ARANGO = {
    "c3_messages": [
        {"_key": "m1", "tenant_id": "t1", "project_id": "p1", "content": "hi"},
        {"_key": "m2", "tenant_id": "t1", "project_id": "p1", "content": "yo"},
    ],
    "memory_sources": [],  # empty slice still recorded
}
_MINIO = {
    "memory": {"sources/t1/p1/s1/doc.md": b"# hello", "sources/t1/p1/s1/raw.bin": b"\x00\x01"},
}


def test_build_parse_roundtrip() -> None:
    gz, _manifest = build_archive(
        scope=BackupScope.PROJECT, tenant_id="t1", project_id="p1",
        arango=_ARANGO, minio=_MINIO, platform_version="0.1.0",
    )
    m2, arango, minio = parse_archive(gz)

    assert m2.scope is BackupScope.PROJECT
    assert m2.tenant_id == "t1"
    assert m2.project_id == "p1"
    assert m2.platform_version == "0.1.0"
    # Round-trip fidelity.
    assert arango == _ARANGO
    assert minio == _MINIO
    # Manifest inventory: counts per store.
    counts = {(e.store, e.name): e.count for e in m2.entries}
    assert counts[(Store.ARANGO, "c3_messages")] == 2
    assert counts[(Store.ARANGO, "memory_sources")] == 0
    assert counts[(Store.MINIO, "memory")] == 2


def test_manifest_returned_by_build_matches_parse() -> None:
    gz, manifest = build_archive(
        scope=BackupScope.PROJECT, tenant_id="t1", project_id="p1",
        arango=_ARANGO, minio=_MINIO,
    )
    parsed, _, _ = parse_archive(gz)
    assert parsed == manifest


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ArchiveIntegrityError):
        parse_archive(b"not a gzip at all")


def test_parse_rejects_tampered_payload() -> None:
    gz, _ = build_archive(
        scope=BackupScope.PROJECT, tenant_id="t1", project_id="p1",
        arango=_ARANGO, minio=_MINIO,
    )
    # Rewrite the c3_messages jsonl member with extra content, keeping the
    # (now-stale) manifest → checksum + count must fail on parse.
    tar_bytes = gzip.decompress(gz)
    out = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as src,
        tarfile.open(fileobj=out, mode="w") as dst,
    ):
        for info in src.getmembers():
            f = src.extractfile(info)
            data = f.read() if f is not None else b""
            if info.name == "arango/c3_messages.jsonl":
                data = data + b'{"_key":"m3","tenant_id":"t1","project_id":"p1"}\n'
                info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    tampered = gzip.compress(out.getvalue())

    with pytest.raises(ArchiveIntegrityError):
        parse_archive(tampered)


def test_parse_rejects_missing_manifest() -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo(name="arango/x.jsonl")
        payload = b"{}\n"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(ArchiveIntegrityError):
        parse_archive(gzip.compress(raw.getvalue()))
