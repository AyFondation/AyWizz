# =============================================================================
# File: test_readers.py
# Version: 1
# Path: ay_platform_core/tests/unit/c16_backup/test_readers.py
# Description: Unit tests for the C16 store-slice readers (R-900-002 / R-900-010)
#              over a fake Arango db: the project filter is applied, `_id`/`_rev`
#              are dropped while `_key` is kept, and secret fields are BLANKED in
#              place (never dropped). Plus the pure MinIO key-in-scope matcher.
#
# @relation validates:R-900-002
# @relation validates:R-900-010
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c16_backup.models import BackupScope, DataMapEntry, Store
from ay_platform_core.c16_backup.readers import (
    _key_in_scope,
    read_arango_project_slice,
)

pytestmark = pytest.mark.unit


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeAql:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_bind: dict[str, Any] | None = None

    def execute(self, query: str, bind_vars: dict[str, Any]) -> _FakeCursor:
        self.last_bind = bind_vars
        # Emulate the tenant/project filter.
        return _FakeCursor([
            r for r in self._rows
            if r.get("tenant_id") == bind_vars["t"]
            and r.get("project_id") == bind_vars["p"]
        ])


class _FakeDB:
    def __init__(self, rows: list[dict[str, Any]], *, exists: bool = True) -> None:
        self.aql = _FakeAql(rows)
        self._exists = exists

    def has_collection(self, name: str) -> bool:
        return self._exists


_SECRET_ENTRY = DataMapEntry(
    store=Store.ARANGO, name="c2_users", component="c2_auth",
    scope=BackupScope.TENANT, tenant_scoped=True, project_scoped=True,
    secret_fields=("argon2id_hash",),
)


def test_reader_blanks_secret_and_drops_sys_fields() -> None:
    db = _FakeDB([
        {"_key": "u1", "_id": "c2_users/u1", "_rev": "abc",
         "tenant_id": "t1", "project_id": "p1", "name": "Alice",
         "argon2id_hash": "$argon2id$SECRET"},
        {"_key": "u2", "tenant_id": "t2", "project_id": "p1", "name": "Other"},
    ])
    rows = read_arango_project_slice(
        db, _SECRET_ENTRY, tenant_id="t1", project_id="p1",  # type: ignore[arg-type]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["_key"] == "u1"  # identity kept
    assert "_id" not in row and "_rev" not in row  # per-instance dropped
    assert row["name"] == "Alice"
    assert row["argon2id_hash"] == ""  # BLANKED, not dropped
    assert "argon2id_hash" in row


def test_reader_absent_collection_returns_empty() -> None:
    db = _FakeDB([], exists=False)
    assert read_arango_project_slice(
        db, _SECRET_ENTRY, tenant_id="t1", project_id="p1",  # type: ignore[arg-type]
    ) == []


@pytest.mark.parametrize(
    ("key", "pid", "expected"),
    [
        ("sources/t1/p1/s1/doc.md", "p1", True),
        ("projects/p1/requirements/spec.md", "p1", True),
        ("validation-reports/p1/run.json", "p1", True),
        ("t1/p1/x/runs/r.json", "p1", True),
        ("projects/p2/requirements/spec.md", "p1", False),
        ("sources/t1/p10/s1/doc.md", "p1", False),  # segment match, not substring
    ],
)
def test_key_in_scope(key: str, pid: str, expected: bool) -> None:
    assert _key_in_scope(key, pid) is expected
