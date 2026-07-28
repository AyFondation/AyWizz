# =============================================================================
# File: test_pricing_repository.py
# Version: 1
# Path: ay_platform_core/tests/integration/c8_llm/test_pricing_repository.py
# Description: Integration tests for PricingRepository against a real ArangoDB
#              (testcontainer). Exercises the DB methods the unit tier cannot
#              (ensure / insert / list_series) and the round-trip that backs the
#              dated pricing source of truth (E-800-004, R-800-141).
#
# @relation validates:R-800-141
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c8_llm.pricing.models import select_effective
from ay_platform_core.c8_llm.pricing.repository import PricingRepository
from ay_platform_core.c8_llm.pricing.service import PricingService
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture(scope="function")
async def pricing_repo(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[PricingRepository]:
    db_name = f"c8_pricing_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    db = client.db(db_name, username="root", password=arango_container.password)
    repo = PricingRepository(db)
    await repo.ensure()
    try:
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


def _doc(mid: str, iso: str, inp: float, out: float) -> dict[str, object]:
    return {
        "_key": uuid.uuid4().hex,
        "model_id": mid,
        "effective_from": iso,
        "input_price_per_mtok": inp,
        "output_price_per_mtok": out,
        "currency": "EUR",
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": "op",
    }


async def test_ensure_is_idempotent(pricing_repo: PricingRepository) -> None:
    # A second ensure() must not raise (collection + index already present).
    await pricing_repo.ensure()


async def test_insert_and_series_round_trip(pricing_repo: PricingRepository) -> None:
    await pricing_repo.insert(_doc("m1", "2026-01-01T00:00:00+00:00", 3.0, 15.0))
    await pricing_repo.insert(_doc("m1", "2026-07-01T00:00:00+00:00", 4.0, 18.0))
    await pricing_repo.insert(_doc("other", "2026-01-01T00:00:00+00:00", 9.0, 9.0))

    series = await pricing_repo.list_series("m1")
    # Only m1's entries, newest effective_from first.
    assert [r["effective_from"] for r in series] == [
        "2026-07-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
    ]
    assert all(r["model_id"] == "m1" for r in series)

    # The real series drives select_effective (R-800-140) end to end, via the
    # public service path (rows → typed entries).
    entries = (await PricingService(pricing_repo).get_series("m1")).entries
    got = select_effective(entries, datetime(2026, 5, 1, tzinfo=UTC))
    assert got is not None and got.input_price_per_mtok == 3.0  # Jan entry in force


async def test_series_empty_for_unknown_model(pricing_repo: PricingRepository) -> None:
    assert await pricing_repo.list_series("nope") == []


async def test_insert_is_append_only(pricing_repo: PricingRepository) -> None:
    # Two entries at the SAME effective_from are BOTH retained (append-only —
    # a correction is a new row, never an overwrite, R-800-141).
    await pricing_repo.insert(_doc("m2", "2026-03-01T00:00:00+00:00", 1.0, 2.0))
    await pricing_repo.insert(_doc("m2", "2026-03-01T00:00:00+00:00", 1.5, 2.5))
    series = await pricing_repo.list_series("m2")
    assert len(series) == 2
    assert {r["input_price_per_mtok"] for r in series} == {1.0, 1.5}
