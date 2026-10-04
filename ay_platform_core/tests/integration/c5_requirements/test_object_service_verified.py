# =============================================================================
# File: test_object_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_object_service_verified.py
# Description: Integration tests for ObjectService against real MinIO +
#              ArangoDB. This is where the rule of 310-SPEC §4.1 is actually
#              demonstrated end to end:
#
#                a negotiation of N iterations, then one decision,
#                produces exactly ONE new version.
#
#              Everything else in increment 1 exists to make that true.
#
# @relation validates:R-310-009
# @relation validates:R-310-010
# @relation validates:R-310-011
# @relation validates:R-310-124
# @relation validates:R-310-150
# @relation validates:R-310-192
# @relation validates:R-310-202
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.objects.locks import LockHeldError, LockManager
from ay_platform_core.c5_requirements.objects.models import (
    HolderKind,
    ObjectReviewRequest,
    ObjectType,
    ReviewDecision,
    ReviewState,
    WorkingDraftWrite,
)
from ay_platform_core.c5_requirements.objects.repository import ObjectRepository
from ay_platform_core.c5_requirements.objects.service import (
    NoDraftError,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectService,
)
from ay_platform_core.c5_requirements.objects.storage import ObjectStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

PID = "adas-brake-ctrl"
ARCH = "030-ARCHITECTURE-DESIGN"
OID = "OBJ-1120"
HUMAN = "o.mathieu"
AGENT = "agent:architect"


@pytest.fixture
def service(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[ObjectService]:
    db_name = f"c5_svc_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ObjectRepository(db)
        repo._ensure_collections_sync()
        locks = LockManager(db, lease_seconds=900)
        locks._ensure_collections_sync()
        yield ObjectService(ObjectStorage(c5_storage), repo, locks)
    finally:
        cleanup_arango_database(arango_container, db_name)


async def _create(service: ObjectService, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "object_id": OID,
        "object_type": ObjectType.PARAGRAPH,
        "actor": AGENT,
        "ordinal": 3,
        "body": "Ramp limited to 120 Nm/s.",
    }
    kwargs.update(overrides)
    return await service.create(PID, ARCH, **kwargs)  # type: ignore[arg-type]


def _draft(body: str, base_version: int = 1) -> WorkingDraftWrite:
    return WorkingDraftWrite(
        type=ObjectType.PARAGRAPH,
        body=body,
        negotiation_id="NEG-0031",
        base_version=base_version,
    )


@pytest.mark.asyncio
async def test_creation_yields_a_proposed_v1_without_a_decision(
    service: ObjectService,
) -> None:
    obj = await _create(service)
    assert obj.version == 1  # type: ignore[attr-defined]
    assert obj.review_state is ReviewState.PROPOSED  # type: ignore[attr-defined]
    # An agent's proposal is not yet a decision; it acquires one at review.
    assert obj.last_review is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_duplicate_identifier_is_refused(service: ObjectService) -> None:
    """R-310-005 — identifiers are never reused, so this is a caller error."""
    await _create(service)
    with pytest.raises(ObjectConflictError):
        await _create(service)


@pytest.mark.asyncio
async def test_negotiation_then_one_decision_yields_exactly_one_version(
    service: ObjectService,
) -> None:
    """THE rule of increment 1 (R-310-009 + R-310-010), demonstrated."""
    await _create(service)
    for attempt in ("try one", "try two", "try three", "try four"):
        await service.write_draft(PID, ARCH, OID, _draft(attempt), actor=AGENT)

    assert await service.list_versions(PID, ARCH, OID) == [1]

    reviewed = await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=1),
        actor=HUMAN,
    )

    assert await service.list_versions(PID, ARCH, OID) == [1, 2]
    assert reviewed.version == 2
    assert reviewed.body == "try four"
    assert reviewed.review_state is ReviewState.ACCEPTED
    assert reviewed.last_review is not None
    assert reviewed.last_review.actor == HUMAN


@pytest.mark.asyncio
async def test_draft_is_discarded_on_acceptance(service: ObjectService) -> None:
    await _create(service)
    await service.write_draft(PID, ARCH, OID, _draft("reworked"), actor=AGENT)
    await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=1),
        actor=HUMAN,
    )
    assert await service.get_draft(PID, ARCH, OID) is None


@pytest.mark.asyncio
async def test_iteration_counter_measures_one_negotiation(
    service: ObjectService,
) -> None:
    await _create(service)
    for i, body in enumerate(("a", "b", "c"), start=1):
        draft = await service.write_draft(PID, ARCH, OID, _draft(body), actor=AGENT)
        assert draft.iteration == i


@pytest.mark.asyncio
async def test_a_new_negotiation_restarts_the_counter(
    service: ObjectService,
) -> None:
    await _create(service)
    await service.write_draft(PID, ARCH, OID, _draft("a"), actor=AGENT)
    second = WorkingDraftWrite(
        type=ObjectType.PARAGRAPH,
        body="fresh start",
        negotiation_id="NEG-0044",
        base_version=1,
    )
    draft = await service.write_draft(PID, ARCH, OID, second, actor=AGENT)
    assert draft.iteration == 1


@pytest.mark.asyncio
async def test_rejection_creates_no_version_and_leaves_the_object_alone(
    service: ObjectService,
) -> None:
    """R-310-124 — a refused draft never became content."""
    await _create(service)
    await service.write_draft(PID, ARCH, OID, _draft("bad rework"), actor=AGENT)

    after = await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.REJECT, expected_version=1),
        actor=HUMAN,
    )

    assert after.version == 1
    assert after.body == "Ramp limited to 120 Nm/s."
    assert await service.list_versions(PID, ARCH, OID) == [1]
    assert await service.get_draft(PID, ARCH, OID) is None


@pytest.mark.asyncio
async def test_confirm_unchanged_records_evidence_as_a_version(
    service: ObjectService,
) -> None:
    """R-310-150 — "examined and unaffected" must be as recorded as a change."""
    await _create(service)
    confirmed = await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(
            decision=ReviewDecision.CONFIRM_UNCHANGED,
            expected_version=1,
            justification="Ramp limit unaffected by the 250 ms timing edit.",
        ),
        actor=HUMAN,
    )
    assert confirmed.version == 2
    assert confirmed.body == "Ramp limited to 120 Nm/s."
    assert confirmed.last_review is not None
    assert confirmed.last_review.decision is ReviewDecision.CONFIRM_UNCHANGED
    assert confirmed.last_review.justification is not None
    assert await service.list_versions(PID, ARCH, OID) == [1, 2]


@pytest.mark.asyncio
async def test_confirm_unchanged_refuses_to_discard_a_live_draft(
    service: ObjectService,
) -> None:
    """Asserting "no change needed" while a rework sits unresolved is a lie."""
    await _create(service)
    await service.write_draft(PID, ARCH, OID, _draft("a rework"), actor=AGENT)
    with pytest.raises(NoDraftError):
        await service.review(
            PID,
            ARCH,
            OID,
            ObjectReviewRequest(
                decision=ReviewDecision.CONFIRM_UNCHANGED,
                expected_version=1,
                justification="nothing to do",
            ),
            actor=HUMAN,
        )


@pytest.mark.asyncio
async def test_auto_accept_is_stored_distinctly(service: ObjectService) -> None:
    """R-310-007 — cluster acceptance must remain separable from review."""
    await _create(service)
    obj = await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.AUTO_ACCEPT, expected_version=1),
        actor="cluster:114",
    )
    assert obj.review_state is ReviewState.AUTO_ACCEPTED
    public = await service.get(PID, ARCH, OID)
    assert public.review_state is ReviewState.AUTO_ACCEPTED


@pytest.mark.asyncio
async def test_stale_expected_version_is_refused_on_draft(
    service: ObjectService,
) -> None:
    """R-310-192 — the version check is the correctness guarantee."""
    await _create(service)
    await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=1),
        actor=HUMAN,
    )
    with pytest.raises(ObjectConflictError) as excinfo:
        await service.write_draft(
            PID, ARCH, OID, _draft("late", base_version=1), actor=AGENT
        )
    assert excinfo.value.expected == 1
    assert excinfo.value.actual == 2


@pytest.mark.asyncio
async def test_stale_expected_version_is_refused_on_review(
    service: ObjectService,
) -> None:
    await _create(service)
    await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=1),
        actor=HUMAN,
    )
    with pytest.raises(ObjectConflictError):
        await service.review(
            PID,
            ARCH,
            OID,
            ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=1),
            actor=HUMAN,
        )


@pytest.mark.asyncio
async def test_a_foreign_lease_blocks_a_draft_write(service: ObjectService) -> None:
    """R-310-191 — an agent holding the lease blocks a human, and vice versa."""
    await _create(service)
    await service.acquire_lock(PID, OID, AGENT, HolderKind.AGENT)
    with pytest.raises(LockHeldError):
        await service.write_draft(PID, ARCH, OID, _draft("mine"), actor=HUMAN)


@pytest.mark.asyncio
async def test_the_lease_holder_may_write(service: ObjectService) -> None:
    await _create(service)
    await service.acquire_lock(PID, OID, AGENT, HolderKind.AGENT)
    draft = await service.write_draft(PID, ARCH, OID, _draft("mine"), actor=AGENT)
    assert draft.written_by == AGENT


@pytest.mark.asyncio
async def test_releasing_the_lease_unblocks_others(service: ObjectService) -> None:
    await _create(service)
    await service.acquire_lock(PID, OID, AGENT, HolderKind.AGENT)
    await service.release_lock(PID, OID, AGENT)
    await service.write_draft(PID, ARCH, OID, _draft("now mine"), actor=HUMAN)


@pytest.mark.asyncio
async def test_public_view_reports_draft_and_lock(service: ObjectService) -> None:
    await _create(service)
    await service.acquire_lock(PID, OID, AGENT, HolderKind.AGENT)
    await service.write_draft(PID, ARCH, OID, _draft("wip"), actor=AGENT)

    public = await service.get(PID, ARCH, OID)
    assert public.has_working_draft is True
    assert public.lock is not None
    assert public.lock.holder == AGENT


@pytest.mark.asyncio
async def test_list_container_is_in_reading_order(service: ObjectService) -> None:
    await _create(service, object_id="OBJ-1126", ordinal=3)
    await _create(service, object_id="OBJ-1118", ordinal=1)
    await _create(service, object_id="OBJ-1120", ordinal=2)
    ids = [o.object_id for o in await service.list_container(PID, ARCH)]
    assert ids == ["OBJ-1118", "OBJ-1120", "OBJ-1126"]


@pytest.mark.asyncio
async def test_unknown_object_raises_not_found(service: ObjectService) -> None:
    with pytest.raises(ObjectNotFoundError):
        await service.get(PID, ARCH, "OBJ-9999")


@pytest.mark.asyncio
async def test_reindex_repairs_an_emptied_index(service: ObjectService) -> None:
    """R-310-002 — the index is a cache; MinIO restores it."""
    await _create(service, object_id="OBJ-1118", ordinal=1)
    await _create(service, object_id="OBJ-1120", ordinal=2)

    service._repo._db.collection("req_objects").truncate()
    assert await service.list_container(PID, ARCH) == []

    written = await service.reindex_container(PID, ARCH)

    assert written == 2
    ids = [o.object_id for o in await service.list_container(PID, ARCH)]
    assert ids == ["OBJ-1118", "OBJ-1120"]


@pytest.mark.asyncio
async def test_retained_versions_are_readable_after_review(
    service: ObjectService,
) -> None:
    await _create(service)
    await service.write_draft(PID, ARCH, OID, _draft("v2 content"), actor=AGENT)
    await service.review(
        PID,
        ARCH,
        OID,
        ObjectReviewRequest(decision=ReviewDecision.ACCEPT, expected_version=1),
        actor=HUMAN,
    )
    first = await service.get_version(PID, ARCH, OID, 1)
    second = await service.get_version(PID, ARCH, OID, 2)
    assert first.body == "Ramp limited to 120 Nm/s."
    assert second.body == "v2 content"
