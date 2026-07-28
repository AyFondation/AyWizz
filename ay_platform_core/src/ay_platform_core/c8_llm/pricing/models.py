# =============================================================================
# File: models.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/pricing/models.py
# Description: Data contracts for the dated model-pricing source of truth
#              (E-800-004). A model's unit cost is an append-only series of
#              effective-dated entries; the price in force at an instant is the
#              entry with the greatest `effective_from` <= that instant
#              (R-800-140). Editing a cost inserts a new entry (R-800-141).
#
# @relation implements:R-800-140
# @relation implements:R-800-141
# =============================================================================

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_CURRENCY = "EUR"


class PricingEntryCreate(BaseModel):
    """Admin request to set a model's unit cost from an effective date."""

    model_config = ConfigDict(extra="forbid")

    input_price_per_mtok: float = Field(ge=0)
    output_price_per_mtok: float = Field(ge=0)
    effective_from: datetime | None = Field(
        default=None,
        description="When this price takes effect. Default: now. A PAST date is "
        "a retroactive correction and triggers replay (R-800-142).",
    )
    currency: str = Field(default=_DEFAULT_CURRENCY, min_length=3, max_length=3)
    note: str = Field(default="", max_length=500)


class PricingEntry(BaseModel):
    """One effective-dated unit-cost entry in the source of truth."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str
    model_id: str
    effective_from: datetime
    input_price_per_mtok: float
    output_price_per_mtok: float
    currency: str = _DEFAULT_CURRENCY
    note: str = ""
    created_at: datetime | None = None
    created_by: str = ""


class PricingSeries(BaseModel):
    """A model's full dated price series (newest first) — history + graph source."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    entries: list[PricingEntry]


def select_effective(
    entries: list[PricingEntry], at: datetime
) -> PricingEntry | None:
    """The pricing entry in force at instant `at` (R-800-140): the entry with
    the greatest `effective_from` that is <= `at`, or `None` when the model was
    unpriced at that instant (all entries are in the future). Pure + total —
    the DB layer fetches the (small) per-model series and applies this."""
    applicable = [e for e in entries if e.effective_from <= at]
    if not applicable:
        return None
    return max(applicable, key=lambda e: e.effective_from)
