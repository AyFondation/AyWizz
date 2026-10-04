# =============================================================================
# File: test_draft_iteration_cap_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_draft_iteration_cap_verified.py
# Description: The working-draft iteration cap — R-310-206.
#
#              THE TEST THAT MATTERS is the one asserting the refused
#              iteration is NOT STORED. The cap exists to surface an agent
#              that rewrites one paragraph without converging; if the
#              anomaly were raised and the work kept, a caller would learn
#              that the cap is advisory and keep going. Reporting and
#              accepting at the same time is the failure mode.
#
#              Also pinned: the counter RESTARTS with a new negotiation.
#              `R-310-206` caps iterations "within one negotiation", and a
#              global counter would eventually refuse every edit to a
#              much-revised object — punishing a healthy history.
#
# @relation validates:R-310-009
# @relation validates:R-310-206
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.objects.locks import LockManager
from ay_platform_core.c5_requirements.objects.models import (
    ObjectType,
    WorkingDraftWrite,
)
from ay_platform_core.c5_requirements.objects.repository import ObjectRepository
from ay_platform_core.c5_requirements.objects.service import (
    DEFAULT_DRAFT_ITERATION_CAP,
    DraftIterationCapError,
    ObjectService,
)
from ay_platform_core.c5_requirements.objects.storage import ObjectStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
PID = "adas"
ARCH = "030-ARCH"
AGENT = "agent:author"
CAP = 3


@pytest.fixture
def service(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[ObjectService]:
    db_name = f"c5_cap_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ObjectRepository(db)
        repo._ensure_collections_sync()
        locks = LockManager(db, lease_seconds=900)
        locks._ensure_collections_sync()
        # A low cap on purpose: the point is the boundary, and exercising a
        # cap of 12 would add nine identical writes to every run.
        yield ObjectService(
            ObjectStorage(c5_storage), repo, locks, draft_iteration_cap=CAP
        )
    finally:
        cleanup_arango_database(arango_container, db_name)


async def _create(service: ObjectService) -> str:
    obj = await service.create(
        PID,
        ARCH,
        object_id="AD-100",
        object_type=ObjectType.PARAGRAPH,
        actor=AGENT,
        ordinal=1,
        body="The controller ramps torque.",
        now=NOW,
    )
    return obj.object_id


async def _iterate(
    service: ObjectService, object_id: str, negotiation: str, text: str
) -> object:
    return await service.write_draft(
        PID,
        ARCH,
        object_id,
        WorkingDraftWrite(
            type=ObjectType.PARAGRAPH,
            body=text,
            negotiation_id=negotiation,
            base_version=1,
        ),
        actor=AGENT,
        now=NOW,
    )


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iterating_up_to_the_cap_is_permitted(
    service: ObjectService,
) -> None:
    object_id = await _create(service)
    for index in range(CAP):
        draft = await _iterate(service, object_id, "n1", f"attempt {index}")
        assert draft.iteration == index + 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_exceeding_the_cap_is_reported_as_an_anomaly(
    service: ObjectService,
) -> None:
    object_id = await _create(service)
    for index in range(CAP):
        await _iterate(service, object_id, "n1", f"attempt {index}")
    with pytest.raises(DraftIterationCapError) as raised:
        await _iterate(service, object_id, "n1", "one too many")
    assert raised.value.cap == CAP
    assert raised.value.negotiation_id == "n1"
    assert "R-310-206" in str(raised.value)


@pytest.mark.asyncio
async def test_the_refused_iteration_is_not_stored(
    service: ObjectService,
) -> None:
    """Reporting the anomaly AND keeping the work teaches that the cap is
    advisory. The check runs before the write for exactly this reason."""
    object_id = await _create(service)
    for index in range(CAP):
        await _iterate(service, object_id, "n1", f"attempt {index}")
    with pytest.raises(DraftIterationCapError):
        await _iterate(service, object_id, "n1", "one too many")

    draft = await service.get_draft(PID, ARCH, object_id)
    assert draft is not None
    assert draft.iteration == CAP
    assert draft.body == f"attempt {CAP - 1}"


@pytest.mark.asyncio
async def test_the_counter_restarts_with_a_new_negotiation(
    service: ObjectService,
) -> None:
    """R-310-206 caps iterations WITHIN one negotiation.

    A global counter would eventually refuse every edit to a much-revised
    object, punishing a healthy history.
    """
    object_id = await _create(service)
    for index in range(CAP):
        await _iterate(service, object_id, "n1", f"attempt {index}")
    fresh = await _iterate(service, object_id, "n2", "a new conversation")
    assert fresh.iteration == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_anomaly_names_the_object_and_what_to_do(
    service: ObjectService,
) -> None:
    object_id = await _create(service)
    for index in range(CAP):
        await _iterate(service, object_id, "n1", f"attempt {index}")
    with pytest.raises(DraftIterationCapError) as raised:
        await _iterate(service, object_id, "n1", "one too many")
    message = str(raised.value)
    assert object_id in message
    assert "Resolve the draft" in message


def test_the_default_cap_is_generous_for_convergence(
    ) -> None:
    """Twelve is plenty for a real exchange and clearly abnormal for a loop."""
    assert DEFAULT_DRAFT_ITERATION_CAP >= 10
