# =============================================================================
# File: service.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/pricing/service.py
# Description: Service over the dated model-pricing source of truth
#              (E-800-004). Owns setting a model's cost (append a dated entry,
#              R-800-141), reading the full series (history + graph, R-800-141),
#              and resolving the price in force at an instant (R-800-140) — the
#              seam the cost computation consumes.
#
# @relation implements:R-800-140
# @relation implements:R-800-141
# =============================================================================

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ay_platform_core.c8_llm.pricing.models import (
    PricingEntry,
    PricingEntryCreate,
    PricingSeries,
    select_effective,
)
from ay_platform_core.c8_llm.pricing.repository import PricingRepository


def _row_to_entry(row: dict[str, object]) -> PricingEntry:
    return PricingEntry(
        entry_id=str(row["_key"]),
        model_id=str(row["model_id"]),
        effective_from=datetime.fromisoformat(str(row["effective_from"])),
        input_price_per_mtok=float(row["input_price_per_mtok"]),  # type: ignore[arg-type]
        output_price_per_mtok=float(row["output_price_per_mtok"]),  # type: ignore[arg-type]
        currency=str(row.get("currency", "EUR")),
        note=str(row.get("note", "")),
        created_at=(
            datetime.fromisoformat(str(row["created_at"]))
            if row.get("created_at")
            else None
        ),
        created_by=str(row.get("created_by", "")),
    )


class PricingService:
    """Set / read / resolve dated model pricing (the source of truth)."""

    def __init__(self, repo: PricingRepository, *, default_currency: str = "EUR") -> None:
        self._repo = repo
        self._default_currency = default_currency

    async def set_price(
        self, model_id: str, req: PricingEntryCreate, actor_id: str
    ) -> PricingEntry:
        """Append a dated pricing entry (never overwrite — R-800-141). A past
        `effective_from` is a retroactive correction; the CALLER is responsible
        for triggering the replay (R-800-142) afterwards."""
        now = datetime.now(UTC)
        effective = req.effective_from or now
        doc = {
            "_key": uuid.uuid4().hex,
            "model_id": model_id,
            "effective_from": effective.isoformat(),
            "input_price_per_mtok": req.input_price_per_mtok,
            "output_price_per_mtok": req.output_price_per_mtok,
            "currency": req.currency or self._default_currency,
            "note": req.note,
            "created_at": now.isoformat(),
            "created_by": actor_id,
        }
        await self._repo.insert(doc)
        return _row_to_entry(doc)

    async def get_series(self, model_id: str) -> PricingSeries:
        """The model's full dated series, newest first (history + graph)."""
        rows = await self._repo.list_series(model_id)
        return PricingSeries(
            model_id=model_id, entries=[_row_to_entry(r) for r in rows]
        )

    async def effective_price(
        self, model_id: str, at: datetime
    ) -> PricingEntry | None:
        """The price in force for `model_id` at instant `at` (R-800-140), or
        `None` when the model was unpriced then."""
        series = await self.get_series(model_id)
        return select_effective(series.entries, at)
