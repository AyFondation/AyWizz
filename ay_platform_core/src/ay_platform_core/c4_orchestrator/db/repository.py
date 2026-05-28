# =============================================================================
# File: repository.py
# Version: 4
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/db/repository.py
# Description: ArangoDB repository for C4. Two collections :
#                - `c4_runs` (E-200-001) : orchestrator state machine
#                  per pipeline run.
#                - `c4_artifact_runs` (R-200-132) : artifact-surface
#                  metadata per run (started_at, status, file_count,
#                  total_bytes, label). The UX lists runs from this
#                  collection — listing MinIO directly would be too
#                  slow for the chip + tree picker.
#              Reuses the lock + overwrite=True pattern established by
#              C5 so concurrent async access via asyncio.to_thread does
#              not deadlock python-arango.
#
#              v3 (2026-05-20) : `read_trace_slice` paginates the run's
#              `trace` array back-in-time per R-200-201. The full trace
#              lives inside the doc (no separate collection) — we slice
#              in Python after a single `get_run` ; AQL-side slicing is
#              not warranted at v1 sizes (O(10²) events per active run).
#
# @relation implements:R-200-080
# @relation implements:R-200-201
# @relation implements:E-200-001
# @relation implements:R-200-132
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar, cast

from arango import ArangoClient  # type: ignore[attr-defined]

COLL_RUNS = "c4_runs"
COLL_ARTIFACT_RUNS = "c4_artifact_runs"

_T = TypeVar("_T")


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'.

    Public reads serialise UTC as '…Z' (Pydantic) while the raw ledger
    stores '…+00:00' (`datetime.isoformat`). Comparing those as strings is
    wrong (`'+' < 'Z'`), so trace pagination compares parsed instants.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class OrchestratorRepository:
    """Sync ArangoDB operations wrapped for async use via asyncio.to_thread.

    See C5 repository for the rationale on `_lock` + `insert(overwrite=True)`:
    python-arango is not thread-safe; concurrent `to_thread` callers
    deadlock without serialisation, and `update()` raises HTTP 412 on
    mismatched `_rev` values after a read-modify-write round.
    """

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
    # Collection bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        if COLL_RUNS not in existing:
            self._db.create_collection(COLL_RUNS)
        if COLL_ARTIFACT_RUNS not in existing:
            self._db.create_collection(COLL_ARTIFACT_RUNS)
        # Indexes on hot-path queries
        self._db.collection(COLL_RUNS).add_index(
            {"type": "persistent", "fields": ["project_id", "session_id"]}
        )
        self._db.collection(COLL_RUNS).add_index(
            {"type": "persistent", "fields": ["status", "started_at"]}
        )
        # Artifact-runs index : the UX hits "runs for project X in
        # tenant Y" on every navigation to /artifacts ; (tenant_id,
        # project_id, started_at desc) covers it without a full scan.
        self._db.collection(COLL_ARTIFACT_RUNS).add_index(
            {
                "type": "persistent",
                "fields": ["tenant_id", "project_id", "started_at"],
            }
        )

    async def ensure_collections(self) -> None:
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Run CRUD
    # ------------------------------------------------------------------

    def _upsert_run_sync(self, run: dict[str, Any]) -> None:
        self._db.collection(COLL_RUNS).insert(run, overwrite=True)

    async def upsert_run(self, run: dict[str, Any]) -> None:
        await self._run(self._upsert_run_sync, run)

    def _get_run_sync(self, run_id: str) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, self._db.collection(COLL_RUNS).get(run_id))

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_run_sync, run_id)

    def _find_active_by_session_sync(
        self, project_id: str, session_id: str
    ) -> dict[str, Any] | None:
        aql = """
        FOR r IN c4_runs
            FILTER r.project_id == @pid AND r.session_id == @sid
                AND r.status == 'running'
            LIMIT 1
            RETURN r
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"pid": project_id, "sid": session_id}
        )
        docs = list(cursor)
        return cast(dict[str, Any] | None, docs[0] if docs else None)

    async def find_active_by_session(
        self, project_id: str, session_id: str
    ) -> dict[str, Any] | None:
        """Used to enforce R-200-002 (one active run per session)."""
        return await self._run(
            self._find_active_by_session_sync, project_id, session_id
        )

    # ------------------------------------------------------------------
    # Trace pagination (R-200-201)
    # ------------------------------------------------------------------

    def _read_trace_slice_sync(
        self, run_id: str, *, before_iso: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        doc = self._db.collection(COLL_RUNS).get(run_id)
        if doc is None:
            return []
        raw = doc.get("trace") if isinstance(doc, dict) else None
        events = raw if isinstance(raw, list) else []
        # Trace is stored append-only (oldest first) ; the public read is
        # newest-first, so we reverse then filter+slice.
        ordered = list(reversed(events))
        if before_iso is not None:
            before_dt = _parse_iso_utc(before_iso)
            ordered = [
                ev for ev in ordered
                if isinstance(ev, dict)
                and isinstance(ev.get("ts"), str)
                and _parse_iso_utc(ev["ts"]) < before_dt
            ]
        return [ev for ev in ordered[:limit] if isinstance(ev, dict)]

    async def read_trace_slice(
        self, run_id: str, *, before_iso: str | None, limit: int,
    ) -> list[dict[str, Any]]:
        """Paginate the run's TraceEvent ledger backwards in time.

        Returns the `limit` most recent events strictly older than
        `before_iso` (ISO-8601 UTC), newest-first. `before_iso=None`
        returns the `limit` most recent events overall.
        """
        return await self._run(
            self._read_trace_slice_sync,
            run_id,
            before_iso=before_iso,
            limit=limit,
        )


    # ------------------------------------------------------------------
    # Artifact-run CRUD (R-200-132)
    # ------------------------------------------------------------------

    def _upsert_artifact_run_sync(self, run: dict[str, Any]) -> None:
        # Same `insert(overwrite=True)` pattern as `c4_runs` — avoids
        # the _rev mismatch on update + serialises through the same
        # lock so concurrent seeder runs can't race.
        self._db.collection(COLL_ARTIFACT_RUNS).insert(run, overwrite=True)

    async def upsert_artifact_run(self, run: dict[str, Any]) -> None:
        await self._run(self._upsert_artifact_run_sync, run)

    def _get_artifact_run_sync(self, run_id: str) -> dict[str, Any] | None:
        return cast(
            dict[str, Any] | None,
            self._db.collection(COLL_ARTIFACT_RUNS).get(run_id),
        )

    async def get_artifact_run(self, run_id: str) -> dict[str, Any] | None:
        return await self._run(self._get_artifact_run_sync, run_id)

    def _list_artifact_runs_sync(
        self, tenant_id: str, project_id: str,
    ) -> list[dict[str, Any]]:
        aql = """
        FOR r IN c4_artifact_runs
            FILTER r.tenant_id == @tid AND r.project_id == @pid
            SORT r.started_at DESC
            RETURN r
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"tid": tenant_id, "pid": project_id},
        )
        return list(cursor)

    async def list_artifact_runs(
        self, tenant_id: str, project_id: str,
    ) -> list[dict[str, Any]]:
        """List the artifact runs of (tenant, project), most recent
        first. Used by `GET /api/v1/projects/{pid}/artifacts/runs`."""
        return await self._run(
            self._list_artifact_runs_sync, tenant_id, project_id,
        )


def make_repository(
    host: str, port: int, username: str, password: str, db_name: str
) -> OrchestratorRepository:
    """Factory used by the FastAPI lifespan and integration tests."""
    client = ArangoClient(hosts=f"http://{host}:{port}")
    db = client.db(db_name, username=username, password=password)
    return OrchestratorRepository(db)
