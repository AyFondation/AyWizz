# =============================================================================
# File: test_remap.py
# Version: 1
# Path: ay_platform_core/tests/unit/c16_backup/test_remap.py
# Description: Unit tests for the restore-as-new id remapper (R-900-008): each
#              KeyStrategy (PROJECT_ID / COMPOSITE / OPAQUE), tenant_id/project_id
#              field rewrite, edge `_from`/`_to` RE-WIRING through the old->new
#              map (referential integrity preserved even for hash-keyed
#              entities), and MinIO object-key remap. A deterministic key factory
#              makes fresh OPAQUE keys assertable.
#
# @relation validates:R-900-008
# =============================================================================

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import pytest

from ay_platform_core.c16_backup.models import BackupScope, DataMapEntry, KeyStrategy, Store
from ay_platform_core.c16_backup.remap import remap_project

pytestmark = pytest.mark.unit


def _entry(name: str, strat: KeyStrategy) -> DataMapEntry:
    return DataMapEntry(
        store=Store.ARANGO, name=name, component="x", scope=BackupScope.PROJECT,
        tenant_scoped=True, project_scoped=True, key_strategy=strat,
    )


_ENTRIES = {
    "c2_projects": _entry("c2_projects", KeyStrategy.PROJECT_ID),
    "c3_messages": _entry("c3_messages", KeyStrategy.OPAQUE),
    "memory_chunks": _entry("memory_chunks", KeyStrategy.COMPOSITE),
    "kg_entities": _entry("kg_entities", KeyStrategy.OPAQUE),
    "kg_relations": _entry("kg_relations", KeyStrategy.OPAQUE),
}


def _fresh_factory() -> Callable[[], str]:
    counter = itertools.count(1)
    return lambda: f"new{next(counter)}"


def test_remap_project_full() -> None:
    arango: dict[str, list[dict[str, Any]]] = {
        "c2_projects": [
            {"_key": "P1", "tenant_id": "T1", "project_id": "P1", "name": "proj"},
        ],
        "c3_messages": [
            {"_key": "msg-abc", "tenant_id": "T1", "project_id": "P1", "body": "hi"},
        ],
        "memory_chunks": [
            {"_key": "T1:P1:c0", "tenant_id": "T1", "project_id": "P1", "v": [1]},
        ],
        "kg_entities": [
            {"_key": "e-hash-1", "tenant_id": "T1", "project_id": "P1", "name": "Foo"},
            {"_key": "e-hash-2", "tenant_id": "T1", "project_id": "P1", "name": "Bar"},
        ],
        "kg_relations": [
            {"_key": "r-hash-1", "tenant_id": "T1", "project_id": "P1",
             "_from": "kg_entities/e-hash-1", "_to": "kg_entities/e-hash-2",
             "rel": "USES"},
        ],
    }
    minio = {"memory": {"sources/T1/P1/s/doc.md": b"x", "sources/T1/P1/s/y.bin": b"z"}}

    r_arango, r_minio = remap_project(
        arango, minio, source_tenant="T1", source_project="P1",
        target_tenant="T2", target_project="P2", entries=_ENTRIES,
        key_factory=_fresh_factory(),
    )

    # PROJECT_ID strategy: key becomes the new project id.
    proj = r_arango["c2_projects"][0]
    assert proj["_key"] == "P2"
    assert proj["tenant_id"] == "T2" and proj["project_id"] == "P2"

    # OPAQUE: fresh key; fields rewritten.
    msg = r_arango["c3_messages"][0]
    assert msg["_key"].startswith("new")
    assert msg["project_id"] == "P2" and msg["tenant_id"] == "T2"
    assert msg["body"] == "hi"  # content untouched

    # COMPOSITE: ids substring-replaced in the key.
    assert r_arango["memory_chunks"][0]["_key"] == "T2:P2:c0"

    # Edge rewiring: _from/_to point at the NEW entity keys (referential
    # integrity preserved despite hash keys being opaque).
    ents = r_arango["kg_entities"]
    new_e1, new_e2 = ents[0]["_key"], ents[1]["_key"]
    rel = r_arango["kg_relations"][0]
    assert rel["_from"] == f"kg_entities/{new_e1}"
    assert rel["_to"] == f"kg_entities/{new_e2}"
    # The relation's endpoints exist in the remapped entity set.
    keys = {e["_key"] for e in ents}
    assert rel["_from"].split("/", 1)[1] in keys
    assert rel["_to"].split("/", 1)[1] in keys

    # MinIO keys remapped.
    assert set(r_minio["memory"]) == {"sources/T2/P2/s/doc.md", "sources/T2/P2/s/y.bin"}


def test_longer_id_replaced_first() -> None:
    # A short id that is a substring of a longer one must not corrupt the swap.
    arango: dict[str, list[dict[str, Any]]] = {
        "memory_chunks": [
            {"_key": "tenantA:tenantA-proj:c0", "tenant_id": "tenantA",
             "project_id": "tenantA-proj", "v": 1},
        ],
    }
    entries = {"memory_chunks": _entry("memory_chunks", KeyStrategy.COMPOSITE)}
    r_arango, _ = remap_project(
        arango, {}, source_tenant="tenantA", source_project="tenantA-proj",
        target_tenant="T2", target_project="P2", entries=entries,
    )
    assert r_arango["memory_chunks"][0]["_key"] == "T2:P2:c0"
