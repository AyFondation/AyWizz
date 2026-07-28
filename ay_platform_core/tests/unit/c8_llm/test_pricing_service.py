# =============================================================================
# File: test_pricing_service.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_pricing_service.py
# Description: Unit tests for PricingService over a fake repository — appending
#              dated entries (R-800-141), reading the series, and resolving the
#              effective price (R-800-140).
#
# @relation validates:R-800-140
# @relation validates:R-800-141
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from ay_platform_core.c8_llm.pricing.models import PricingEntryCreate
from ay_platform_core.c8_llm.pricing.repository import PricingRepository
from ay_platform_core.c8_llm.pricing.service import PricingService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeRepo:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def ensure(self) -> None:
        pass

    async def insert(self, doc: dict[str, Any]) -> None:
        self.docs.append(doc)

    async def list_series(self, model_id: str) -> list[dict[str, Any]]:
        rows = [d for d in self.docs if d["model_id"] == model_id]
        return sorted(rows, key=lambda d: d["effective_from"], reverse=True)


def _svc() -> tuple[PricingService, _FakeRepo]:
    repo = _FakeRepo()
    return PricingService(cast(PricingRepository, repo)), repo


async def test_set_price_appends_dated_entry() -> None:
    svc, repo = _svc()
    entry = await svc.set_price(
        "m1",
        PricingEntryCreate(
            input_price_per_mtok=3.0,
            output_price_per_mtok=15.0,
            effective_from=datetime(2026, 4, 1, tzinfo=UTC),
            note="q2",
        ),
        actor_id="op",
    )
    assert entry.model_id == "m1"
    assert entry.effective_from == datetime(2026, 4, 1, tzinfo=UTC)
    assert entry.created_by == "op" and entry.note == "q2"
    assert len(repo.docs) == 1  # appended, not overwritten


async def test_set_price_defaults_effective_from_to_now() -> None:
    svc, _ = _svc()
    before = datetime.now(UTC)
    entry = await svc.set_price(
        "m1",
        PricingEntryCreate(input_price_per_mtok=1.0, output_price_per_mtok=2.0),
        actor_id="op",
    )
    assert entry.effective_from >= before


async def test_effective_price_selects_in_force_entry() -> None:
    svc, _ = _svc()
    await svc.set_price(
        "m1",
        PricingEntryCreate(
            input_price_per_mtok=3.0,
            output_price_per_mtok=15.0,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        actor_id="op",
    )
    await svc.set_price(
        "m1",
        PricingEntryCreate(
            input_price_per_mtok=4.0,
            output_price_per_mtok=18.0,
            effective_from=datetime(2026, 7, 1, tzinfo=UTC),
        ),
        actor_id="op",
    )
    got = await svc.effective_price("m1", datetime(2026, 5, 1, tzinfo=UTC))
    assert got is not None and got.input_price_per_mtok == 3.0  # Jan entry
    series = await svc.get_series("m1")
    assert [e.input_price_per_mtok for e in series.entries] == [4.0, 3.0]  # newest first


async def test_effective_price_none_when_unpriced() -> None:
    svc, _ = _svc()
    assert await svc.effective_price("absent", datetime.now(UTC)) is None
