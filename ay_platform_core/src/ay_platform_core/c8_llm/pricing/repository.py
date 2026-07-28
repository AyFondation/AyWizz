# =============================================================================
# File: repository.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/pricing/repository.py
# Description: ArangoDB persistence for the dated model-pricing source of truth
#              (E-800-004). Append-only per model; the price in force at an
#              instant is selected with `pricing.models.select_effective` over
#              the (small) per-model series. python-arango is synchronous →
#              wrapped in `asyncio.to_thread`.
# =============================================================================

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast

from arango.exceptions import CollectionCreateError

COLL_PRICING = "c8_model_pricing"


class PricingRepository:
    """Sync ArangoDB operations for `c8_model_pricing`, async-wrapped."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def _ensure_sync(self) -> None:
        with contextlib.suppress(CollectionCreateError):
            self._db.create_collection(COLL_PRICING)
        # Persistent index for the per-model series scan (idempotent).
        self._db.collection(COLL_PRICING).add_index(
            {
                "type": "persistent",
                "fields": ["model_id", "effective_from"],
                "unique": False,
            }
        )

    async def ensure(self) -> None:
        await asyncio.to_thread(self._ensure_sync)

    def _insert_sync(self, doc: dict[str, Any]) -> None:
        self._db.collection(COLL_PRICING).insert(doc)

    async def insert(self, doc: dict[str, Any]) -> None:
        """Append a pricing entry (never overwrite — R-800-141)."""
        await asyncio.to_thread(self._insert_sync, doc)

    def _series_sync(self, model_id: str) -> list[dict[str, Any]]:
        cursor = self._db.aql.execute(
            "FOR p IN @@coll FILTER p.model_id == @mid "
            "SORT p.effective_from DESC RETURN p",
            bind_vars={"@coll": COLL_PRICING, "mid": model_id},
        )
        return cast("list[dict[str, Any]]", list(cursor))

    async def list_series(self, model_id: str) -> list[dict[str, Any]]:
        """The model's full dated series, newest first (history + graph)."""
        return await asyncio.to_thread(self._series_sync, model_id)
