# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/process/repository.py
# Description: ArangoDB derived index for cycle (`C-`) and workflow (`WF-`)
#              versions — 310-SPEC §4.2 / §4.3, E-310-006.
#
#              Answers the one question MinIO answers badly: "which version
#              of this entity is currently approved, at this scope?" Version
#              files are immutable and self-describing, so the index holds
#              metadata only and is rebuildable from them (R-310-002 applies
#              here by the same logic).
#
#              Follows the C5 repository pattern: sync python-arango calls
#              wrapped for async use and serialised by an asyncio.Lock.
#
# @relation implements:R-310-020
# @relation implements:R-310-040
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar, cast

from .models import CycleDefinition, EntityStatus, WorkflowDefinition

_T = TypeVar("_T")

COLL_CYCLES = "req_cycles"
COLL_WORKFLOWS = "req_workflows"


def scope_key(tenant_id: str, project_id: str | None) -> str:
    """Return the scope component of an index key.

    Tenant scope is where the standard catalogue lives; project scope is
    where a tailored override lives (R-310-022).
    """
    return f"t:{tenant_id}" if project_id is None else f"p:{project_id}"


def index_key(
    tenant_id: str, project_id: str | None, entity_id: str, version: int
) -> str:
    """Return the ArangoDB `_key` of one entity version's index row."""
    return f"{scope_key(tenant_id, project_id)}:{entity_id}:v{version}"


def to_cycle_row(definition: CycleDefinition) -> dict[str, Any]:
    """Project a cycle version onto its index row (metadata only)."""
    row = _common_row(definition, definition.cycle_id)
    row["title"] = definition.title
    row["container_slugs"] = [c.slug for c in definition.ordered_containers]
    row["container_count"] = len(definition.containers)
    return row


def to_workflow_row(definition: WorkflowDefinition) -> dict[str, Any]:
    """Project a workflow version onto its index row (metadata only)."""
    row = _common_row(definition, definition.workflow_id)
    row["intent"] = definition.intent
    row["step_count"] = len(definition.steps)
    row["check_ids"] = [c.check_id for c in definition.checks]
    return row


def _common_row(
    definition: CycleDefinition | WorkflowDefinition, entity_id: str
) -> dict[str, Any]:
    return {
        "_key": index_key(
            definition.tenant_id,
            definition.project_id,
            entity_id,
            definition.version,
        ),
        "entity_id": entity_id,
        "version": definition.version,
        "status": definition.status.value,
        "tenant_id": definition.tenant_id,
        "project_id": definition.project_id,
        "scope": scope_key(definition.tenant_id, definition.project_id),
        "tailoring_of": definition.tailoring_of,
        "updated_at": definition.updated_at.isoformat(),
        "updated_by": definition.updated_by,
    }


class ProcessRepository:
    """Derived-index operations for cycle and workflow versions.

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
        existing = {c["name"] for c in self._db.collections()}
        for name in (COLL_CYCLES, COLL_WORKFLOWS):
            if name not in existing:
                self._db.create_collection(name)
            # "Which version is approved at this scope" is the hot read.
            self._db.collection(name).add_index(
                {"type": "persistent", "fields": ["scope", "entity_id", "status"]}
            )

    async def ensure_collections(self) -> None:
        """Create the process collections and their indexes if absent."""
        await self._run(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _upsert_sync(self, collection: str, row: dict[str, Any]) -> None:
        self._db.collection(collection).insert(row, overwrite=True)

    async def upsert_cycle(self, definition: CycleDefinition) -> None:
        """Index (or re-index) one cycle version."""
        await self._run(self._upsert_sync, COLL_CYCLES, to_cycle_row(definition))

    async def upsert_workflow(self, definition: WorkflowDefinition) -> None:
        """Index (or re-index) one workflow version."""
        await self._run(self._upsert_sync, COLL_WORKFLOWS, to_workflow_row(definition))

    def _supersede_sync(self, collection: str, scope: str, entity_id: str) -> int:
        aql = f"""
        FOR e IN {collection}
            FILTER e.scope == @scope
               AND e.entity_id == @eid
               AND e.status == "approved"
            UPDATE e WITH {{ status: "superseded" }} IN {collection}
            COLLECT WITH COUNT INTO n
            RETURN n
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"scope": scope, "eid": entity_id}
        )
        rows = list(cursor)
        return int(rows[0]) if rows else 0

    async def supersede_approved(
        self, tenant_id: str, project_id: str | None, entity_id: str, *, is_cycle: bool
    ) -> int:
        """Mark the currently approved version of an entity superseded.

        Only the INDEX row changes: the sealed MinIO document keeps the
        status it was published with, because a published version is
        immutable (R-310-023). "Superseded" is a statement about which
        version is current, not a rewrite of what was published.

        Returns:
            The number of rows transitioned (0 or 1 in practice).
        """
        return await self._run(
            self._supersede_sync,
            COLL_CYCLES if is_cycle else COLL_WORKFLOWS,
            scope_key(tenant_id, project_id),
            entity_id,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _approved_sync(
        self, collection: str, scope: str, entity_id: str
    ) -> dict[str, Any] | None:
        aql = f"""
        FOR e IN {collection}
            FILTER e.scope == @scope
               AND e.entity_id == @eid
               AND e.status == "approved"
            SORT e.version DESC
            LIMIT 1
            RETURN e
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"scope": scope, "eid": entity_id}
        )
        rows = list(cursor)
        return cast(dict[str, Any] | None, rows[0] if rows else None)

    async def approved_version(
        self, tenant_id: str, project_id: str | None, entity_id: str, *, is_cycle: bool
    ) -> dict[str, Any] | None:
        """Return the approved version row of an entity at this scope, or None."""
        return await self._run(
            self._approved_sync,
            COLL_CYCLES if is_cycle else COLL_WORKFLOWS,
            scope_key(tenant_id, project_id),
            entity_id,
        )

    def _list_sync(
        self, collection: str, scope: str, entity_id: str | None
    ) -> list[dict[str, Any]]:
        aql = f"""
        FOR e IN {collection}
            FILTER e.scope == @scope
               AND (@eid == null OR e.entity_id == @eid)
            SORT e.entity_id ASC, e.version ASC
            RETURN e
        """
        cursor = self._db.aql.execute(aql, bind_vars={"scope": scope, "eid": entity_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def list_versions(
        self,
        tenant_id: str,
        project_id: str | None,
        entity_id: str | None = None,
        *,
        is_cycle: bool,
    ) -> list[dict[str, Any]]:
        """Return index rows at this scope, optionally for one entity."""
        return await self._run(
            self._list_sync,
            COLL_CYCLES if is_cycle else COLL_WORKFLOWS,
            scope_key(tenant_id, project_id),
            entity_id,
        )

    def _get_sync(self, collection: str, key: str) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, self._db.collection(collection).get(key))

    async def get_version(
        self,
        tenant_id: str,
        project_id: str | None,
        entity_id: str,
        version: int,
        *,
        is_cycle: bool,
    ) -> dict[str, Any] | None:
        """Return one version's index row, or None."""
        return await self._run(
            self._get_sync,
            COLL_CYCLES if is_cycle else COLL_WORKFLOWS,
            index_key(tenant_id, project_id, entity_id, version),
        )

    def _next_version_sync(self, collection: str, scope: str, entity_id: str) -> int:
        aql = f"""
        FOR e IN {collection}
            FILTER e.scope == @scope AND e.entity_id == @eid
            COLLECT AGGREGATE top = MAX(e.version)
            RETURN top
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"scope": scope, "eid": entity_id}
        )
        rows = list(cursor)
        highest = rows[0] if rows and rows[0] is not None else 0
        return int(highest) + 1

    async def next_version(
        self, tenant_id: str, project_id: str | None, entity_id: str, *, is_cycle: bool
    ) -> int:
        """Return the next free version number for an entity at this scope.

        Derived from the index rather than tracked as a counter: a counter
        would be a second source of truth able to drift from what is stored.
        """
        return await self._run(
            self._next_version_sync,
            COLL_CYCLES if is_cycle else COLL_WORKFLOWS,
            scope_key(tenant_id, project_id),
            entity_id,
        )


#: Re-exported so callers can compare against the enum rather than a literal.
APPROVED = EntityStatus.APPROVED
