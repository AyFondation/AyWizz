# =============================================================================
# File: repository.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c6_validation/db/repository.py
# Description: ArangoDB repository for C6. Collections: c6_runs (one doc per
#              validation run) + c6_findings (one doc per finding, keyed by
#              finding_id). Uses the lock pattern from C5/C7 since python-
#              arango is not thread-safe.
#
#              v2 (D-017 / R-700-033): adds `get_latest_completed_run` so the
#              service can detect quality regressions against a project's most
#              recent prior run.
#
# @relation implements:R-700-012
# @relation implements:R-700-033
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar, cast

COLL_RUNS = "c6_runs"
COLL_FINDINGS = "c6_findings"

_T = TypeVar("_T")


class ValidationRepository:
    """Sync ArangoDB ops wrapped for async use via asyncio.to_thread."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _run(self, func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        async with self._get_lock():
            return await asyncio.to_thread(func, *args, **kwargs)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        for name in (COLL_RUNS, COLL_FINDINGS):
            if name not in existing:
                self._db.create_collection(name)
        self._db.collection(COLL_RUNS).add_index(
            {"type": "persistent", "fields": ["project_id", "domain"]}
        )
        self._db.collection(COLL_FINDINGS).add_index(
            {"type": "persistent", "fields": ["run_id"]}
        )
        self._db.collection(COLL_FINDINGS).add_index(
            {"type": "persistent", "fields": ["check_id"]}
        )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def _upsert_run_sync(self, run: dict[str, Any]) -> None:
        self._db.collection(COLL_RUNS).insert(
            {**run, "_key": run["run_id"]}, overwrite=True
        )

    async def upsert_run(self, run: dict[str, Any]) -> None:
        await self._run(self._upsert_run_sync, run)

    def _get_run_sync(self, run_id: str) -> dict[str, Any] | None:
        coll = self._db.collection(COLL_RUNS)
        doc = coll.get(run_id)
        return cast(dict[str, Any] | None, doc)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_run_sync, run_id)

    def _get_latest_completed_run_sync(
        self, project_id: str, domain: str
    ) -> dict[str, Any] | None:
        # Most recent COMPLETED run for (project, domain). The run currently
        # executing is still `running`, so it is naturally excluded — this
        # returns the genuine previous run (R-700-033).
        aql = """
        FOR r IN c6_runs
            FILTER r.project_id == @project_id
            FILTER r.domain == @domain
            FILTER r.status == "completed"
            FILTER r.completed_at != null
            SORT r.completed_at DESC
            LIMIT 1
            RETURN UNSET(r, ["_id", "_key", "_rev"])
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"project_id": project_id, "domain": domain}
        )
        rows = list(cursor)
        return cast(dict[str, Any], rows[0]) if rows else None

    async def get_latest_completed_run(
        self, project_id: str, domain: str
    ) -> dict[str, Any] | None:
        return await self._run(
            self._get_latest_completed_run_sync, project_id, domain
        )

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------

    def _insert_findings_sync(self, findings: list[dict[str, Any]]) -> None:
        if not findings:
            return
        keyed = [{**f, "_key": f["finding_id"]} for f in findings]
        self._db.collection(COLL_FINDINGS).insert_many(keyed, overwrite=True)

    async def insert_findings(self, findings: list[dict[str, Any]]) -> None:
        await self._run(self._insert_findings_sync, findings)

    def _list_findings_for_run_sync(
        self, run_id: str, limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]:
        aql = """
        LET total = LENGTH(
            FOR f IN c6_findings
                FILTER f.run_id == @run_id
                RETURN 1
        )
        LET items = (
            FOR f IN c6_findings
                FILTER f.run_id == @run_id
                SORT f.created_at ASC
                LIMIT @offset, @limit
                RETURN UNSET(f, ["_id", "_key", "_rev"])
        )
        RETURN { total, items }
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={"run_id": run_id, "limit": limit, "offset": offset},
        )
        row = next(iter(cursor))
        return int(row["total"]), cast(list[dict[str, Any]], list(row["items"]))

    async def list_findings_for_run(
        self, run_id: str, limit: int = 100, offset: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        return await self._run(
            self._list_findings_for_run_sync, run_id, limit, offset
        )

    def _get_finding_sync(self, finding_id: str) -> dict[str, Any] | None:
        doc = self._db.collection(COLL_FINDINGS).get(finding_id)
        if doc is None:
            return None
        doc = dict(doc)
        for k in ("_id", "_key", "_rev"):
            doc.pop(k, None)
        return doc

    async def get_finding(self, finding_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_finding_sync, finding_id)
