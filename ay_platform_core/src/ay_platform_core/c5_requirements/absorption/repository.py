# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/absorption/repository.py
# Description: The derived index of change tickets — E-310-006, R-310-149.
#
#              THE ROW HAS NO STATUS FIELD. `R-310-149` forbids exposing
#              manual status mutation, and the cheapest way for that rule
#              to rot is a stored `status` string that someone later
#              updates directly. Open-ness is `closed_at == null`, which
#              cannot disagree with the ticket it indexes.
#
#              NOR AN ASSIGNEE, PRIORITY OR DUE DATE. Their absence is the
#              requirement; a test asserts it on the row projection as
#              well as on the model, because an index is exactly where
#              such a field would be added "just for the UI".
#
#              METADATA ONLY. MinIO is the source of truth for a ticket
#              (`R-310-002`); these rows exist so "what is open in this
#              project?" is one query rather than a bucket scan, and the
#              whole collection is rebuildable from MinIO.
#
#              THIS MODULE DOES NOT QUERY `req_object_edges`. That
#              collection has an owner — `coverage/repository.py` — and
#              the batched reverse read the traversal needs lives there as
#              `links_to_targets`. Two AQL writers against one collection
#              is how query semantics drift.
#
# @relation implements:R-310-140
# @relation implements:R-310-149
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .models import ChangeTicket

_T = TypeVar("_T")

COLL_CHANGES = "req_changes"


def to_change_row(ticket: ChangeTicket) -> dict[str, Any]:
    """Project a ticket onto its index row.

    Note the fields that are absent: no `status` (derived from
    `closed_at`), no assignee, no priority. See the module docstring.
    """
    return {
        "_key": ticket.ticket_id,
        "project_id": ticket.project_id,
        "drop_id": ticket.drop_id,
        "requirement_id": ticket.requirement_id,
        "kind": ticket.kind.value,
        "opened_at": ticket.opened_at.isoformat(),
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
        "closed_by": ticket.closed_by,
        "node_count": ticket.node_count,
        "dispositioned_count": ticket.dispositioned_count,
        "confirmed_unchanged_count": ticket.confirmed_unchanged_count,
    }


class AbsorptionRepository:
    """The queryable index of change tickets.

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
        if COLL_CHANGES not in existing:
            self._db.create_collection(COLL_CHANGES)

    async def ensure_collections(self) -> None:
        """Create the change index if it is absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _upsert_sync(self, row: dict[str, Any]) -> None:
        self._db.collection(COLL_CHANGES).insert(row, overwrite=True)

    async def put_change(self, ticket: ChangeTicket) -> None:
        """Index one ticket, replacing any earlier row for it.

        Keyed on the derived ticket id, so re-running the same diff
        refreshes one row instead of opening a second ticket
        (`R-310-140`).
        """
        await self._run(self._upsert_sync, to_change_row(ticket))

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _list_sync(
        self, project_id: str, only_open: bool, requirement_id: str | None
    ) -> list[dict[str, Any]]:
        aql = """
        FOR c IN req_changes
            FILTER c.project_id == @pid
               AND (@only_open == false OR c.closed_at == null)
               AND (@requirement == null OR c.requirement_id == @requirement)
            SORT c.opened_at ASC, c._key ASC
            RETURN c
        """
        cursor = self._db.aql.execute(
            aql,
            bind_vars={
                "pid": project_id,
                "only_open": only_open,
                "requirement": requirement_id,
            },
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_changes(
        self,
        project_id: str,
        *,
        only_open: bool = False,
        requirement_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return indexed tickets, newest last.

        Args:
            project_id: Owning project.
            only_open: Restrict to tickets with no closure recorded.
            requirement_id: Restrict to one supplied requirement.
        """
        return await self._run(self._list_sync, project_id, only_open, requirement_id)

    def _open_count_sync(self, project_id: str) -> int:
        aql = """
        RETURN COUNT(
            FOR c IN req_changes
                FILTER c.project_id == @pid AND c.closed_at == null
                RETURN 1
        )
        """
        cursor = self._db.aql.execute(aql, bind_vars={"pid": project_id})
        return cast(int, next(iter(cursor)))

    async def count_open(self, project_id: str) -> int:
        """Return how many tickets remain open in a project."""
        return await self._run(self._open_count_sync, project_id)

    def _drop_sync(self, ticket_id: str) -> bool:
        collection = self._db.collection(COLL_CHANGES)
        if not collection.has(ticket_id):
            return False
        collection.delete(ticket_id)
        return True

    async def drop_change(self, ticket_id: str) -> bool:
        """Remove one index row.

        For index rebuilds only — a ticket itself is never deleted, since
        a closed ticket is the evidence that a supplied modification was
        absorbed (`R-310-150`).
        """
        return await self._run(self._drop_sync, ticket_id)
