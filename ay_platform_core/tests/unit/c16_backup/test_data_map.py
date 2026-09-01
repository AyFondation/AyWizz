# =============================================================================
# File: test_data_map.py
# Version: 1
# Path: ay_platform_core/tests/unit/c16_backup/test_data_map.py
# Description: Unit tests for the C16 DataMap structure + models (E-900-001..003
#              / R-900-001): scope invariants, no duplicate/overlapping names,
#              secret fields well-formed, per-scope selection, and the manifest/
#              record model validation.
#
# @relation validates:R-900-001
# @relation validates:E-900-001
# @relation validates:E-900-002
# @relation validates:E-900-003
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c16_backup.data_map import (
    DATA_MAP,
    EXCLUDED,
    entries_for_scope,
)
from ay_platform_core.c16_backup.models import (
    BackupManifest,
    BackupRecord,
    BackupScope,
    ManifestEntry,
    Origin,
    Store,
)

pytestmark = pytest.mark.unit


def test_no_duplicate_names_per_store() -> None:
    for store in Store:
        names = [e.name for e in DATA_MAP if e.store is store]
        assert len(names) == len(set(names)), f"duplicate DataMap name in {store}"


def test_included_and_excluded_are_disjoint() -> None:
    for store in Store:
        inc = {e.name for e in DATA_MAP if e.store is store}
        exc = {e.name for e in EXCLUDED if e.store is store}
        assert not (inc & exc), f"name classified BOTH included+excluded: {inc & exc}"


def test_scope_invariants() -> None:
    for e in DATA_MAP:
        if e.scope is BackupScope.PROJECT:
            assert e.project_scoped, f"{e.name}: PROJECT scope must be project_scoped"
        if e.scope is BackupScope.TENANT:
            assert e.tenant_scoped, f"{e.name}: TENANT scope must be tenant_scoped"


def test_project_snapshot_is_subset_of_tenant_snapshot() -> None:
    tenant = set(entries_for_scope(BackupScope.TENANT))
    project = set(entries_for_scope(BackupScope.PROJECT))
    assert project <= tenant
    # A project snapshot captures ONLY project-scoped entries.
    assert all(e.scope is BackupScope.PROJECT for e in project)
    # A tenant snapshot captures everything.
    assert tenant == set(DATA_MAP)


def test_secret_fields_only_declared_where_expected() -> None:
    # The one known secret-bearing INCLUDED store is c2_users (password hash).
    secret_entries = {e.name for e in DATA_MAP if e.secret_fields}
    assert secret_entries == {"c2_users"}
    users = next(e for e in DATA_MAP if e.name == "c2_users")
    assert "argon2id_hash" in users.secret_fields


def test_entry_is_frozen() -> None:
    e = DATA_MAP[0]
    with pytest.raises(ValidationError):
        e.name = "mutated"


# ---- Model validation ------------------------------------------------------


def test_manifest_roundtrips() -> None:
    m = BackupManifest(
        manifest_version=1,
        scope=BackupScope.PROJECT,
        tenant_id="t1",
        project_id="p1",
        created_at="2026-08-31T00:00:00+00:00",
        platform_version="0.1.0",
        entries=[ManifestEntry(store=Store.ARANGO, name="c3_messages",
                               count=3, sha256="a" * 64)],
    )
    again = BackupManifest.model_validate_json(m.model_dump_json())
    assert again == m


def test_manifest_entry_rejects_bad_checksum() -> None:
    with pytest.raises(ValidationError):
        ManifestEntry(store=Store.ARANGO, name="x", count=0, sha256="short")


def test_backup_record_requires_fields() -> None:
    rec = BackupRecord(
        backup_id="b1", scope=BackupScope.TENANT, tenant_id="t1",
        created_at="2026-08-31T00:00:00+00:00", created_by="alice",
        size_bytes=10, object_key="tenant/t1/b1.tar.gz", checksum="b" * 64,
        origin=Origin.GENERATED, manifest_version=1,
    )
    assert rec.project_id is None
    with pytest.raises(ValidationError):
        BackupRecord(
            backup_id="b2", scope=BackupScope.TENANT, tenant_id="t1",
            created_at="x", created_by="a", size_bytes=-1,
            object_key="k", checksum="c" * 64, origin=Origin.GENERATED,
            manifest_version=1,
        )
