# =============================================================================
# File: test_backup_data_map.py
# Version: 1
# Path: ay_platform_core/tests/coherence/test_backup_data_map.py
# Description: Coherence guard for the C16 backup DataMap (R-900-001): every
#              ArangoDB collection defined ANYWHERE in the codebase MUST be
#              classified in the DataMap — either INCLUDED (backed up) or
#              EXCLUDED (with a reason). A new component's collection cannot
#              silently escape backup: adding a `COLL_* = "..."` without a
#              DataMap decision fails this test.
#
# @relation validates:R-900-001
# =============================================================================

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ay_platform_core.c16_backup.data_map import EXCLUDED, mapped_names
from ay_platform_core.c16_backup.models import Store

pytestmark = pytest.mark.coherence

_SRC = Path(__file__).resolve().parents[2] / "src" / "ay_platform_core"

# Matches `COLL_X = "collection_name"` and `COLLECTION = "collection_name"`
# (the two idioms used across the component repositories).
_COLL_RE = re.compile(r'(?:COLL[A-Z0-9_]*|COLLECTION)\s*=\s*"([a-z0-9_]+)"')

# Curated set of per-tenant/project MinIO buckets (bucket defaults are declared
# in varied ways across component configs, so they are pinned explicitly rather
# than scanned). Update alongside the DataMap when a new bucket lands.
_EXPECTED_BUCKETS: frozenset[str] = frozenset({
    "memory", "requirements", "orchestrator", "validation",
    "llm-archive", "c13-extractor-artifacts",
})


def _discovered_collections() -> set[str]:
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        for m in _COLL_RE.finditer(path.read_text(encoding="utf-8")):
            found.add(m.group(1))
    return found


def test_every_collection_is_classified() -> None:
    """Completeness: every collection in the codebase is either backed up or
    explicitly excluded — nothing escapes silently."""
    discovered = _discovered_collections()
    classified = mapped_names(Store.ARANGO)
    unclassified = discovered - classified
    assert not unclassified, (
        "ArangoDB collection(s) found in the codebase but NOT classified in the "
        "C16 DataMap (data_map.py). Each MUST be added to DATA_MAP (backed up) "
        f"or EXCLUDED (with a reason): {sorted(unclassified)}"
    )


def test_no_stale_classified_collection() -> None:
    """No stale entries: every collection the DataMap classifies really exists
    in the codebase (catches typos / removed collections)."""
    discovered = _discovered_collections()
    classified = mapped_names(Store.ARANGO)
    stale = classified - discovered
    assert not stale, (
        "DataMap classifies collection(s) that no longer exist in the codebase "
        f"(typo or removed): {sorted(stale)}"
    )


def test_minio_buckets_match_expected() -> None:
    """The DataMap's MinIO classification covers exactly the known per-
    tenant/project buckets (pinned set)."""
    assert mapped_names(Store.MINIO) == _EXPECTED_BUCKETS


def test_excluded_entries_have_reasons() -> None:
    for ex in EXCLUDED:
        assert ex.reason.strip(), f"excluded store {ex.name!r} has no reason"
