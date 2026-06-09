# =============================================================================
# File: repository.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/repository.py
# Description: ArangoDB persistence for the quota subsystem. v2 supports the
#              four-level model: usage is aggregated over `llm_calls` filtered by
#              an optional subject (tenant / project / user) — no filter = the
#              global platform aggregate. Also returns the oldest call timestamp
#              in the window (for the first-use reset countdown). python-arango
#              is synchronous → wrapped in `asyncio.to_thread`.
# =============================================================================

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

COLL_POLICY = "llm_quota_policy"
COLL_CALLS = "llm_calls"
COLL_ANCHORS = "llm_quota_anchors"
POLICY_KEY = "global"

_T = TypeVar("_T")


@runtime_checkable
class QuotaStore(Protocol):
    """Storage seam the QuotaService depends on (concrete = Arango, fake in
    tests)."""

    async def get_policy(self) -> dict[str, Any] | None: ...
    async def set_policy(self, doc: dict[str, Any]) -> None: ...
    async def get_anchor(self, key: str) -> str | None: ...
    async def set_anchor(self, key: str, iso: str) -> None: ...
    async def usage_in_window(
        self,
        since_iso: str,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[float, int, str | None]: ...


class QuotaRepository:
    """Concrete `QuotaStore` over the shared C8 Arango database."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def _run(self, fn: Callable[..., _T], *args: Any) -> _T:
        return await asyncio.to_thread(fn, *args)

    # ---- Policy (single global document) ---------------------------------

    def _get_policy_sync(self) -> dict[str, Any] | None:
        if not self._db.has_collection(COLL_POLICY):
            return None
        return cast(
            "dict[str, Any] | None", self._db.collection(COLL_POLICY).get(POLICY_KEY)
        )

    async def get_policy(self) -> dict[str, Any] | None:
        return await self._run(self._get_policy_sync)

    def _set_policy_sync(self, doc: dict[str, Any]) -> None:
        if not self._db.has_collection(COLL_POLICY):
            self._db.create_collection(COLL_POLICY)
        coll = self._db.collection(COLL_POLICY)
        body = {**doc, "_key": POLICY_KEY}
        if coll.has(POLICY_KEY):
            coll.replace(body)
        else:
            coll.insert(body)

    async def set_policy(self, doc: dict[str, Any]) -> None:
        await self._run(self._set_policy_sync, doc)

    # ---- First-use session anchors (`{window}:{level}:{subject}`) ---------

    def _get_anchor_sync(self, key: str) -> str | None:
        if not self._db.has_collection(COLL_ANCHORS):
            return None
        doc = self._db.collection(COLL_ANCHORS).get(key)
        anchor = doc.get("anchor") if isinstance(doc, dict) else None
        return anchor if isinstance(anchor, str) else None

    async def get_anchor(self, key: str) -> str | None:
        return await self._run(self._get_anchor_sync, key)

    def _set_anchor_sync(self, key: str, iso: str) -> None:
        if not self._db.has_collection(COLL_ANCHORS):
            self._db.create_collection(COLL_ANCHORS)
        self._db.collection(COLL_ANCHORS).insert(
            {"_key": key, "anchor": iso}, overwrite=True
        )

    async def set_anchor(self, key: str, iso: str) -> None:
        await self._run(self._set_anchor_sync, key, iso)

    # ---- Usage aggregation (per-level filtered window) --------------------

    def _usage_in_window_sync(
        self,
        since_iso: str,
        tenant_id: str | None,
        project_id: str | None,
        user_id: str | None,
    ) -> tuple[float, int, str | None]:
        if not self._db.has_collection(COLL_CALLS):
            return (0.0, 0, None)
        # Build the subject filter incrementally; no subject = global aggregate.
        filters = ["c.timestamp_start >= @since"]
        bind: dict[str, Any] = {"@col": COLL_CALLS, "since": since_iso}
        if tenant_id is not None:
            filters.append("c.tags.tenant_id == @tid")
            bind["tid"] = tenant_id
        if project_id is not None:
            filters.append("c.tags.project_id == @pid")
            bind["pid"] = project_id
        if user_id is not None:
            filters.append("c.tags.user_id == @uid")
            bind["uid"] = user_id
        query = (
            "FOR c IN @@col "
            f"  FILTER {' AND '.join(filters)} "
            "  COLLECT AGGREGATE cost = SUM(c.cost_usd), "
            "          toks = SUM(c.input_tokens + c.output_tokens), "
            "          oldest = MIN(c.timestamp_start) "
            "  RETURN { cost, toks, oldest }"
        )
        rows = list(self._db.aql.execute(query, bind_vars=bind))
        if not rows:
            return (0.0, 0, None)
        row = rows[0]
        oldest = row.get("oldest")
        return (
            float(row.get("cost") or 0.0),
            int(row.get("toks") or 0),
            oldest if isinstance(oldest, str) else None,
        )

    async def usage_in_window(
        self,
        since_iso: str,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[float, int, str | None]:
        """Sum (cost_usd, input+output tokens) of `llm_calls` with
        `timestamp_start >= since_iso`, filtered to the given subject (any
        combination of tenant/project/user; all None = the global platform
        aggregate), plus the oldest `timestamp_start` in the window. Empty →
        (0.0, 0, None)."""
        return await self._run(
            self._usage_in_window_sync, since_iso, tenant_id, project_id, user_id
        )
