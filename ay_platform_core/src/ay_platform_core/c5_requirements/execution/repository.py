# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/execution/repository.py
# Description: The derived index of treatment plans — E-310-006.
#
#              METADATA ONLY, as everywhere else in C5: MinIO is the source
#              of truth (`R-310-002`) and these rows exist so "what is
#              awaiting ratification in this project?" is one query rather
#              than a bucket scan.
#
#              THE ROW RECORDS `ratified_version`, NOT `is_ratified`. The
#              two differ exactly when a plan has been amended past its
#              approval (`R-310-173`), and a boolean would read `true` for
#              a plan whose current terms nobody approved. Storing the
#              version keeps the index incapable of making that claim.
#
# @relation implements:R-310-171
# @relation implements:R-310-175
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .models import TreatmentPlan

_T = TypeVar("_T")

COLL_PLANS = "req_plans"


def to_plan_row(plan: TreatmentPlan) -> dict[str, Any]:
    """Project a plan onto its index row.

    `ratified_version` rather than a boolean: see the module docstring.
    """
    total = plan.total_estimate
    return {
        "_key": f"{plan.project_id}:{plan.plan_id}",
        "plan_id": plan.plan_id,
        "project_id": plan.project_id,
        "version": plan.version,
        "batch_kind": plan.batch.kind.value,
        "batch_source": plan.batch.source,
        "unit_count": plan.unit_count,
        "step_count": len(plan.steps),
        "proposed_by": plan.proposed_by,
        "proposed_at": plan.proposed_at.isoformat(),
        "ratified_version": (
            plan.ratification.plan_version if plan.ratification else None
        ),
        "ratified_by": plan.ratification.actor if plan.ratification else None,
        "ratified_at": (
            plan.ratification.at.isoformat() if plan.ratification else None
        ),
        "estimated_tokens": total.tokens,
        "estimated_cost_eur": total.cost_eur,
        "estimated_review_items": total.review_items,
        "is_complete": plan.is_complete,
        "suspended_step_count": len(plan.suspended_step_ids),
    }


class ExecutionRepository:
    """The queryable index of treatment plans.

    Args:
        db: A python-arango `Database` handle.
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
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {collection["name"] for collection in self._db.collections()}
        if COLL_PLANS not in existing:
            self._db.create_collection(COLL_PLANS)

    async def ensure_collections(self) -> None:
        """Create the plan index if it is absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _upsert_sync(self, row: dict[str, Any]) -> None:
        self._db.collection(COLL_PLANS).insert(row, overwrite=True)

    async def put_plan(self, plan: TreatmentPlan) -> None:
        """Index one plan, replacing any earlier row for it."""
        await self._run(self._upsert_sync, to_plan_row(plan))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _list_sync(
        self, project_id: str, awaiting_only: bool, active_only: bool
    ) -> list[dict[str, Any]]:
        aql = """
        FOR p IN req_plans
            FILTER p.project_id == @pid
               AND (@awaiting == false
                    OR p.ratified_version == null
                    OR p.ratified_version != p.version)
               AND (@active == false OR p.is_complete == false)
            SORT p.proposed_at ASC, p._key ASC
            RETURN p
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "pid": project_id,
                "awaiting": awaiting_only,
                "active": active_only,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_plans(
        self,
        project_id: str,
        *,
        awaiting_ratification: bool = False,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return indexed plans, oldest first.

        Args:
            project_id: Owning project.
            awaiting_ratification: Restrict to plans whose CURRENT version
                carries no ratification — which includes a plan amended
                past its approval, not only a never-approved one.
            active_only: Restrict to plans with work outstanding.
        """
        return await self._run(
            self._list_sync, project_id, awaiting_ratification, active_only
        )

    def _suspended_sync(self, project_id: str) -> list[dict[str, Any]]:
        aql = """
        FOR p IN req_plans
            FILTER p.project_id == @pid AND p.suspended_step_count > 0
            SORT p._key ASC
            RETURN p
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def list_suspended(self, project_id: str) -> list[dict[str, Any]]:
        """Return plans with a step suspended on an overrun (`R-310-174`).

        Its own query because a suspended step is waiting on the user and
        would otherwise sit unnoticed among active plans — which is the
        failure `R-310-174` exists to prevent.
        """
        return await self._run(self._suspended_sync, project_id)

    def _drop_sync(self, key: str) -> bool:
        collection = self._db.collection(COLL_PLANS)
        if not collection.has(key):
            return False
        collection.delete(key)
        return True

    async def drop_plan(self, project_id: str, plan_id: str) -> bool:
        """Remove one index row.

        For index rebuilds only — a plan itself is never deleted, since a
        ratification is an attributable record against work performed
        (`R-310-175`).
        """
        return await self._run(self._drop_sync, f"{project_id}:{plan_id}")
