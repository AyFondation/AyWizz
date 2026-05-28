# =============================================================================
# File: test_bitemporal.py
# Version: 1
# Path: ay_platform_core/tests/integration/c7_memory/test_bitemporal.py
# Description: V2 #3-C integration tests — bi-temporal KG (D-019 / R-400-209)
#              over a real ArangoDB. Persist a structural relation with a
#              valid-time, then SUPERSEDE it (append-only), and assert the
#              as-of queries return the right version on each axis :
#                - transaction time (known_as_of) : V1 before the correction,
#                  V2 after ; nothing deleted ;
#                - valid time (valid_at) : V1's interval was closed at the
#                  correction instant, V2 is open-ended.
#
# @relation validates:R-400-209
# =============================================================================

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c7_memory.kg.ontology import (
    StructuralEntity,
    StructuralExtraction,
    StructuralRelation,
)
from ay_platform_core.c7_memory.kg.repository import KGRepository
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_TID, _PID = "tenant-bt", "project-bt"
_T0 = datetime(2020, 1, 1, tzinfo=UTC)  # V1 valid-from (well in the past)


@pytest_asyncio.fixture(scope="function")
async def kg(arango_container: ArangoEndpoint) -> AsyncIterator[KGRepository]:
    db_name = f"c7_bt_{uuid.uuid4().hex[:8]}"
    ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    ).create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = KGRepository(db)
    repo._ensure_collections_sync()
    try:
        yield repo
    finally:
        cleanup_arango_database(arango_container, db_name)


def _relation(valid_from: datetime | None) -> StructuralExtraction:
    subj = StructuralEntity(name="modA", type="MODULE")
    obj = StructuralEntity(name="R-1", type="REQUIREMENT")
    rel = StructuralRelation(
        subject=subj, type="IMPLEMENTS", object=obj, valid_from=valid_from,
    )
    return StructuralExtraction(entities=[subj, obj], relations=[rel])


async def test_supersede_then_as_of_on_both_axes(kg: KGRepository) -> None:
    # V1 : modA IMPLEMENTS R-1, valid from 2020.
    await kg.persist_structural(
        tenant_id=_TID, project_id=_PID, source_id="s1",
        extraction=_relation(_T0),
    )
    await asyncio.sleep(0.05)
    mid = datetime.now(UTC)  # between V1 and the correction
    await asyncio.sleep(0.05)

    # Correction : re-assert with a new valid-from = now (append-only).
    new_valid_from = datetime.now(UTC)
    await kg.supersede_relation(
        tenant_id=_TID, project_id=_PID,
        subject_name="modA", subject_type="MODULE",
        relation="IMPLEMENTS", object_name="R-1", object_type="REQUIREMENT",
        valid_from=new_valid_from, source_id="s2",
    )

    # Nothing deleted : both versions persist.
    everything = await kg.relations_as_of(tenant_id=_TID, project_id=_PID)
    assert len(everything) == 2

    # Transaction axis : as known at `mid`, only V1 (open then) is current.
    at_mid = await kg.relations_as_of(
        tenant_id=_TID, project_id=_PID, known_as_of=mid,
    )
    assert len(at_mid) == 1
    assert at_mid[0]["valid_from"] == _T0.isoformat()
    assert at_mid[0]["superseded_at"] is None or at_mid[0]["superseded_at"] > mid.isoformat()

    # As known NOW, only V2 (the current assertion) — V1 is superseded.
    now_known = await kg.relations_as_of(
        tenant_id=_TID, project_id=_PID, known_as_of=datetime.now(UTC),
    )
    assert len(now_known) == 1
    assert now_known[0]["superseded_at"] is None
    assert now_known[0]["valid_from"] == new_valid_from.isoformat()


async def test_valid_at_returns_world_time_truth(kg: KGRepository) -> None:
    await kg.persist_structural(
        tenant_id=_TID, project_id=_PID, source_id="s1",
        extraction=_relation(_T0),
    )
    await asyncio.sleep(0.02)
    await kg.supersede_relation(
        tenant_id=_TID, project_id=_PID,
        subject_name="modA", subject_type="MODULE",
        relation="IMPLEMENTS", object_name="R-1", object_type="REQUIREMENT",
        valid_from=datetime.now(UTC), source_id="s2",
    )

    # Valid at 2021 : inside V1's [2020, correction) interval, not yet V2.
    at_2021 = await kg.relations_as_of(
        tenant_id=_TID, project_id=_PID, valid_at=datetime(2021, 1, 1, tzinfo=UTC),
    )
    assert len(at_2021) == 1
    assert at_2021[0]["valid_from"] == _T0.isoformat()

    # Valid far in the future : only V2 (open-ended) holds ; V1 was closed.
    future = datetime(2099, 1, 1, tzinfo=UTC)
    at_future = await kg.relations_as_of(
        tenant_id=_TID, project_id=_PID, valid_at=future,
    )
    assert len(at_future) == 1
    assert at_future[0]["valid_to"] is None


async def test_persist_stamps_temporal_columns(kg: KGRepository) -> None:
    await kg.persist_structural(
        tenant_id=_TID, project_id=_PID, source_id="s1",
        extraction=_relation(_T0),
    )
    rels = await kg.list_relations_for_source(_TID, _PID, "s1")
    assert len(rels) == 1
    r = rels[0]
    assert r["valid_from"] == _T0.isoformat()
    assert r["valid_to"] is None
    assert r["recorded_at"] is not None
    assert r["superseded_at"] is None
