# =============================================================================
# File: test_pricing_select.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_pricing_select.py
# Description: Unit tests for the effective-price selection (R-800-140): the
#              price in force at an instant is the dated entry with the greatest
#              `effective_from` <= that instant. Pure function, no DB.
#
# @relation validates:R-800-140
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ay_platform_core.c8_llm.pricing.models import PricingEntry, select_effective

pytestmark = pytest.mark.unit


def _entry(eid: str, iso: str, inp: float, out: float) -> PricingEntry:
    return PricingEntry(
        entry_id=eid,
        model_id="m1",
        effective_from=datetime.fromisoformat(iso),
        input_price_per_mtok=inp,
        output_price_per_mtok=out,
    )


_SERIES = [
    _entry("a", "2026-01-01T00:00:00+00:00", 3.0, 15.0),
    _entry("b", "2026-04-01T00:00:00+00:00", 3.5, 16.0),
    _entry("c", "2026-07-01T00:00:00+00:00", 4.0, 18.0),
]


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def test_empty_series_is_unpriced() -> None:
    assert select_effective([], _at("2026-05-01T00:00:00+00:00")) is None


def test_all_future_entries_is_unpriced() -> None:
    # The instant precedes every effective_from → no price in force.
    assert select_effective(_SERIES, _at("2025-12-31T23:59:59+00:00")) is None


def test_picks_greatest_effective_from_not_after_instant() -> None:
    got = select_effective(_SERIES, _at("2026-05-15T00:00:00+00:00"))
    assert got is not None and got.entry_id == "b"  # April, not July


def test_boundary_is_inclusive() -> None:
    # at == effective_from → that entry is in force.
    got = select_effective(_SERIES, _at("2026-07-01T00:00:00+00:00"))
    assert got is not None and got.entry_id == "c"


def test_latest_when_instant_after_all() -> None:
    got = select_effective(_SERIES, _at("2027-01-01T00:00:00+00:00"))
    assert got is not None and got.entry_id == "c"


def test_order_independent() -> None:
    # Selection depends on effective_from, not list order.
    shuffled = [_SERIES[2], _SERIES[0], _SERIES[1]]
    got = select_effective(shuffled, _at("2026-05-15T00:00:00+00:00"))
    assert got is not None and got.entry_id == "b"


def test_now_uses_epoch_free_input() -> None:
    # A tz-aware `now` resolves to the latest past entry (regression guard for
    # naive/aware datetime mixups — all inputs are tz-aware here).
    got = select_effective(_SERIES, datetime.now(UTC))
    assert got is not None and got.entry_id == "c"
