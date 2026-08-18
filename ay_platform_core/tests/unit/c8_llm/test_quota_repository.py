# =============================================================================
# File: test_quota_repository.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_quota_repository.py
# Description: Unit tests for the ArangoDB-backed `QuotaRepository` AQL layer —
#              the consumption aggregations (R-800-144/145) and the per-request
#              model-mix breakdown (R-800-146). A fake `db` records the AQL +
#              bind vars so we assert (1) row → tuple mapping, (2) TENANT
#              SCOPING is applied to the query when a tenant is given (no
#              cross-tenant leak), and (3) the breakdown field whitelist rejects
#              anything outside {run_id, turn_id} (the field is interpolated
#              into AQL — an injection guard). The endpoint/service layers are
#              covered separately over an in-memory store; this file covers the
#              query bodies those fakes bypass.
#
# @relation validates:R-800-145
# @relation validates:R-800-146
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c8_llm.quota.repository import COLL_CALLS, QuotaRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeAql:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_query: str | None = None
        self.last_bind: dict[str, Any] = {}

    def execute(self, query: str, bind_vars: dict[str, Any] | None = None) -> Any:
        self.last_query = query
        self.last_bind = dict(bind_vars or {})
        return iter(self._rows)


class _FakeDb:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, has: bool = True) -> None:
        self.aql = _FakeAql(rows or [])
        self._has = has
        self.checked: list[str] = []

    def has_collection(self, name: str) -> bool:
        self.checked.append(name)
        return self._has


def _repo(
    rows: list[dict[str, Any]] | None = None, *, has: bool = True
) -> tuple[QuotaRepository, _FakeDb]:
    db = _FakeDb(rows, has=has)
    return QuotaRepository(db), db


# --- consumption_by_tenant -------------------------------------------------


async def test_consumption_by_tenant_maps_rows_and_drops_null() -> None:
    repo, db = _repo(
        [
            {"tenant": "t-a", "cost": 1.5, "toks": 300},
            {"tenant": None, "cost": 9.0, "toks": 900},  # dropped (no tenant)
        ]
    )
    out = await repo.consumption_by_tenant("2026-01-01T00:00:00Z")
    assert out == [("t-a", 1.5, 300)]
    assert db.checked == [COLL_CALLS]


async def test_consumption_by_tenant_missing_collection_is_empty() -> None:
    repo, _ = _repo(has=False)
    assert await repo.consumption_by_tenant("2026-01-01T00:00:00Z") == []


async def test_consumption_by_tenant_coerces_missing_aggregates_to_zero() -> None:
    repo, _ = _repo([{"tenant": "t-a"}])  # no cost/toks keys
    assert await repo.consumption_by_tenant("2026-01-01T00:00:00Z") == [("t-a", 0.0, 0)]


# --- consumption_by_project / _by_user : tenant scoping --------------------


async def test_consumption_by_project_platform_wide_has_no_tenant_filter() -> None:
    repo, db = _repo([{"project": "p1", "cost": 2.0, "toks": 200}])
    out = await repo.consumption_by_project("2026-01-01T00:00:00Z")
    assert out == [("p1", 2.0, 200)]
    assert "tid" not in db.aql.last_bind
    assert "c.tags.tenant_id == @tid" not in (db.aql.last_query or "")


async def test_consumption_by_project_scoped_applies_tenant_filter() -> None:
    repo, db = _repo([{"project": "p1", "cost": 2.0, "toks": 200}])
    await repo.consumption_by_project("2026-01-01T00:00:00Z", tenant_id="t-a")
    assert db.aql.last_bind["tid"] == "t-a"
    assert "c.tags.tenant_id == @tid" in (db.aql.last_query or "")


async def test_consumption_by_project_missing_collection_is_empty() -> None:
    repo, _ = _repo(has=False)
    assert await repo.consumption_by_project("2026-01-01T00:00:00Z", tenant_id="t") == []


async def test_consumption_by_user_scoped_applies_tenant_filter() -> None:
    repo, db = _repo([{"user": "u1", "cost": 3.0, "toks": 30}])
    out = await repo.consumption_by_user("2026-01-01T00:00:00Z", tenant_id="t-a")
    assert out == [("u1", 3.0, 30)]
    assert db.aql.last_bind["tid"] == "t-a"
    assert "c.tags.tenant_id == @tid" in (db.aql.last_query or "")


async def test_consumption_by_user_missing_collection_is_empty() -> None:
    repo, _ = _repo(has=False)
    assert await repo.consumption_by_user("2026-01-01T00:00:00Z", tenant_id="t") == []


# --- breakdown_by_model : whitelist guard + scoping + mapping --------------


async def test_breakdown_by_model_maps_four_tuples() -> None:
    repo, db = _repo(
        [
            {"model": "opus", "intok": 100, "outtok": 50, "cost": 1.0},
            {"model": "haiku", "intok": 300, "outtok": 200, "cost": 0.2},
        ]
    )
    out = await repo.breakdown_by_model("run_id", "run-1")
    assert out == [("opus", 100, 50, 1.0), ("haiku", 300, 200, 0.2)]
    assert db.aql.last_bind["val"] == "run-1"


async def test_breakdown_by_model_accepts_turn_id_and_scopes_tenant() -> None:
    repo, db = _repo([{"model": "opus", "intok": 1, "outtok": 2, "cost": 0.1}])
    await repo.breakdown_by_model("turn_id", "turn-9", tenant_id="t-a")
    assert db.aql.last_bind["tid"] == "t-a"
    assert "c.tags.turn_id == @val" in (db.aql.last_query or "")


async def test_breakdown_by_model_rejects_non_whitelisted_field() -> None:
    """The field is interpolated into AQL — anything outside the whitelist is
    an injection risk and MUST raise before touching the db."""
    repo, db = _repo([{"model": "x"}])
    with pytest.raises(ValueError, match="unsupported breakdown field"):
        await repo.breakdown_by_model("model", "opus")  # not run_id/turn_id
    assert db.aql.last_query is None  # never reached the db


async def test_breakdown_by_model_missing_collection_is_empty() -> None:
    repo, _ = _repo(has=False)
    assert await repo.breakdown_by_model("run_id", "run-1") == []
