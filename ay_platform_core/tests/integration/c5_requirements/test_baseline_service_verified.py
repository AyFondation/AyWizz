# =============================================================================
# File: test_baseline_service_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_baseline_service_verified.py
# Description: Taking and resolving a baseline against real MinIO +
#              ArangoDB — R-310-200, R-310-201, R-310-204.
#
#              What this tier establishes that no lower one can:
#                - the gate refuses on each of its three preconditions
#                  INDEPENDENTLY and reports all of them together;
#                - a tag cannot be reused — the manifest write refuses an
#                  existing one, which is immutability as a property of the
#                  store rather than a convention (R-310-204);
#                - `resolve` re-hashes what it reads and refuses a
#                  mismatch, so a reference that rotted is loud;
#                - a baseline includes `accepted` and `auto-accepted` and
#                  EXCLUDES `proposed` — content nobody agreed to is not
#                  part of a photograph of what the project decided.
#
# @relation validates:R-310-200
# @relation validates:R-310-201
# @relation validates:R-310-204
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from arango import ArangoClient  # type: ignore[attr-defined]

from ay_platform_core.c5_requirements.baseline.models import (
    BaselineBlocker,
    BaselineManifest,
)
from ay_platform_core.c5_requirements.baseline.repository import BaselineRepository
from ay_platform_core.c5_requirements.baseline.service import (
    BaselineNotFoundError,
    BaselineRefusedError,
    BaselineService,
    ManifestIntegrityError,
)
from ay_platform_core.c5_requirements.baseline.storage import (
    BaselineExistsError,
    BaselineStorage,
)
from ay_platform_core.c5_requirements.objects.models import (
    DocObjectPublic,
    ObjectType,
    ReviewState,
)
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
TENANT = "acme"
PID = "adas"
CYCLE = "C-AUTO"
FUNC = "020-FUNC"
ARCH = "030-ARCH"
OWNER = "o.mathieu"


def _public(
    object_id: str,
    container: str,
    ordinal: int,
    body: str,
    state: ReviewState = ReviewState.ACCEPTED,
) -> DocObjectPublic:
    return DocObjectPublic(
        object_id=object_id,
        container=container,
        type=ObjectType.PARAGRAPH,
        ordinal=ordinal,
        version=2,
        review_state=state,
        body=body,
        created_at=NOW,
        created_by=OWNER,
        updated_at=NOW,
        updated_by=OWNER,
    )


class World:
    """Everything the baseline service asks another surface, driven here."""

    def __init__(self) -> None:
        self.open_tickets: list[str] = []
        self.suspect: list[dict[str, object]] = []
        self.gaps: list[tuple[str, str]] = []
        self.cycle_version = 2
        self.containers: list[str] = [FUNC, ARCH]
        self.objects: dict[str, list[DocObjectPublic]] = {
            FUNC: [
                _public("FD-010", FUNC, 10, "Functional description"),
                _public(
                    "FD-011",
                    FUNC,
                    11,
                    "Deceleration shall reach zero within 150 ms.",
                ),
            ],
            ARCH: [
                _public(
                    "AD-100",
                    ARCH,
                    30,
                    "The controller ramps torque at 140 Nm/s.",
                    ReviewState.AUTO_ACCEPTED,
                ),
                _public(
                    "AD-101",
                    ARCH,
                    31,
                    "Draft nobody has accepted.",
                    ReviewState.PROPOSED,
                ),
            ],
        }
        self.links: dict[str, list[dict[str, object]]] = {
            FUNC: [],
            ARCH: [
                {
                    "object_id": "AD-100",
                    "target_id": "CUST-001",
                    "pinned_version": 4,
                    "strength": "covered",
                    "state": "accepted",
                },
                {
                    # A pin on the PROPOSED object: excluded with it, and the
                    # manifest validator would refuse it if it leaked through.
                    "object_id": "AD-101",
                    "target_id": "CUST-002",
                    "pinned_version": 1,
                    "strength": "covered",
                    "state": "proposed",
                },
            ],
        }

    async def tickets_of(self, project_id: str) -> Sequence[str]:
        return self.open_tickets

    async def suspect_of(self, project_id: str) -> Sequence[dict[str, object]]:
        return self.suspect

    async def gaps_of(self, project_id: str) -> Sequence[tuple[str, str]]:
        return self.gaps

    async def cycle_of(
        self, tenant_id: str, project_id: str, cycle_id: str
    ) -> tuple[int, Sequence[str]]:
        return self.cycle_version, self.containers

    async def objects_of(
        self, project_id: str, container: str
    ) -> Sequence[DocObjectPublic]:
        return self.objects.get(container, [])

    async def links_of(
        self, project_id: str, container: str
    ) -> Sequence[dict[str, object]]:
        return self.links.get(container, [])


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[BaselineService, World, BaselineStorage]]:
    db_name = f"c5_base_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = BaselineRepository(db)
        repo._ensure_collections_sync()
        world = World()
        storage = BaselineStorage(c5_storage)
        service = BaselineService(
            storage,
            repo,
            world.tickets_of,
            world.suspect_of,
            world.gaps_of,
            world.cycle_of,
            world.objects_of,
            world.links_of,
        )
        yield service, world, storage
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
def service(wired: tuple[BaselineService, World, BaselineStorage]) -> BaselineService:
    return wired[0]


@pytest.fixture
def world(wired: tuple[BaselineService, World, BaselineStorage]) -> World:
    return wired[1]


@pytest.fixture
def storage(wired: tuple[BaselineService, World, BaselineStorage]) -> BaselineStorage:
    return wired[2]


async def _take(service: BaselineService, tag: str = "B-01") -> BaselineManifest:
    return await service.create(
        TENANT, PID, tag, cycle_id=CYCLE, actor=OWNER, now=NOW
    )


# ---------------------------------------------------------------------------
# R-310-201 — the gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_project_is_ready(service: BaselineService) -> None:
    assert (await service.readiness(PID)).is_ready is True


@pytest.mark.asyncio
async def test_an_open_change_ticket_blocks_a_baseline(
    service: BaselineService, world: World
) -> None:
    world.open_tickets = ["adas:W14:CUST-001"]
    verdict = await service.readiness(PID)
    assert verdict.blockers == {BaselineBlocker.OPEN_CHANGE_TICKET}
    assert "adas:W14:CUST-001" in verdict.explain()


@pytest.mark.asyncio
async def test_a_rated_coverage_gap_blocks_a_baseline(
    service: BaselineService, world: World
) -> None:
    world.gaps = [("CUST-009", "ASIL-D")]
    verdict = await service.readiness(PID)
    assert verdict.blockers == {BaselineBlocker.CRITICAL_COVERAGE_GAP}
    assert "ASIL-D" in verdict.explain()


@pytest.mark.asyncio
async def test_a_stale_link_blocks_a_baseline_and_names_both_versions(
    service: BaselineService, world: World
) -> None:
    world.suspect = [
        {
            "object_id": "AD-100",
            "target_id": "CUST-001",
            "pinned_version": 2,
            "current_version": 7,
        }
    ]
    verdict = await service.readiness(PID)
    assert verdict.blockers == {BaselineBlocker.STALE_COVERAGE_LINK}
    assert "pins v2" in verdict.explain()
    assert "v7" in verdict.explain()


@pytest.mark.asyncio
async def test_every_blocker_is_reported_at_once(
    service: BaselineService, world: World
) -> None:
    """A reviewer shown one obstacle at a time comes back twice more."""
    world.open_tickets = ["adas:W14:CUST-001"]
    world.gaps = [("CUST-009", "ASIL-D")]
    world.suspect = [
        {"object_id": "AD-100", "target_id": "CUST-001", "pinned_version": 1, "current_version": 2}
    ]
    assert (await service.readiness(PID)).blockers == set(BaselineBlocker)


@pytest.mark.asyncio
async def test_creation_is_refused_while_the_gate_is_closed(
    service: BaselineService, world: World
) -> None:
    world.open_tickets = ["adas:W14:CUST-001"]
    with pytest.raises(BaselineRefusedError) as raised:
        await _take(service)
    assert raised.value.readiness.blockers == {BaselineBlocker.OPEN_CHANGE_TICKET}


# ---------------------------------------------------------------------------
# R-310-200 — what a baseline names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_baseline_names_the_accepted_objects_of_every_container(
    service: BaselineService,
) -> None:
    await _take(service)
    manifest = await service.get(PID, "B-01")
    assert manifest.object_count == 3
    assert set(manifest.containers) == {FUNC, ARCH}


@pytest.mark.asyncio
async def test_a_proposed_object_is_excluded(service: BaselineService) -> None:
    """A photograph of what the project DECIDED, not of what an agent wrote."""
    await _take(service)
    manifest = await service.get(PID, "B-01")
    assert "AD-101" not in {entry.object_id for entry in manifest.objects}


@pytest.mark.asyncio
async def test_an_auto_accepted_object_is_included_and_stays_distinguishable(
    service: BaselineService,
) -> None:
    """R-310-007: it is acceptance, and the state is recorded per entry."""
    await _take(service)
    manifest = await service.get(PID, "B-01")
    entry = manifest.entry("AD-100")
    assert entry.review_state == "auto-accepted"


@pytest.mark.asyncio
async def test_a_pin_on_an_excluded_object_is_dropped_with_it(
    service: BaselineService,
) -> None:
    await _take(service)
    manifest = await service.get(PID, "B-01")
    assert manifest.links_of("AD-101") == ()
    assert manifest.links_of("AD-100")[0].target_id == "CUST-001"


@pytest.mark.asyncio
async def test_the_manifest_records_the_cycle_it_photographed(
    service: BaselineService,
) -> None:
    await _take(service)
    manifest = await service.get(PID, "B-01")
    assert manifest.cycle_id == CYCLE
    assert manifest.cycle_version == 2


@pytest.mark.asyncio
async def test_a_baseline_is_listed_after_it_is_taken(
    service: BaselineService,
) -> None:
    await _take(service, "B-01")
    await _take(service, "B-02")
    assert await service.list_tags(PID) == ("B-01", "B-02")


@pytest.mark.asyncio
async def test_an_unknown_tag_is_reported_missing(service: BaselineService) -> None:
    with pytest.raises(BaselineNotFoundError, match="B-99"):
        await service.get(PID, "B-99")


# ---------------------------------------------------------------------------
# R-310-204 — immutability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tag_cannot_be_reused(service: BaselineService) -> None:
    """Immutability as a property of the store, not a convention."""
    await _take(service, "B-01")
    with pytest.raises(BaselineExistsError, match="immutable"):
        await _take(service, "B-01")


@pytest.mark.asyncio
async def test_the_storage_offers_no_way_to_delete_a_manifest(
    storage: BaselineStorage,
) -> None:
    """An operation that can destroy an audit record will eventually be called."""
    for forbidden in ("delete", "delete_manifest", "drop", "remove", "purge"):
        assert not hasattr(storage, forbidden)


# ---------------------------------------------------------------------------
# Resolution — the hash is checked, not trusted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolving_pairs_each_entry_with_the_content_it_names(
    service: BaselineService,
) -> None:
    await _take(service)
    resolved = await service.resolve(PID, "B-01")
    texts = {item.entry.object_id: item.content for item in resolved}
    assert "150 ms" in texts["FD-011"]
    assert "140 Nm/s" in texts["AD-100"]


@pytest.mark.asyncio
async def test_content_changing_under_a_baseline_is_refused_loudly(
    service: BaselineService, world: World
) -> None:
    """A reference nobody checks is a reference that silently rots."""
    await _take(service)
    world.objects[ARCH][0] = _public(
        "AD-100", ARCH, 30, "Rewritten without a new version.",
        ReviewState.AUTO_ACCEPTED,
    )
    with pytest.raises(ManifestIntegrityError, match="no longer hashes"):
        await service.resolve(PID, "B-01")


@pytest.mark.asyncio
async def test_a_version_moving_on_is_reported_rather_than_rendered(
    service: BaselineService, world: World
) -> None:
    """Rendering today's text under yesterday's tag is the failure to avoid."""
    await _take(service)
    moved = world.objects[ARCH][0].model_copy(update={"version": 5})
    world.objects[ARCH][0] = moved
    with pytest.raises(ManifestIntegrityError, match="only v5 was read"):
        await service.resolve(PID, "B-01")


@pytest.mark.asyncio
async def test_a_deleted_version_is_reported_as_the_violation_it_is(
    service: BaselineService, world: World
) -> None:
    await _take(service)
    world.objects[ARCH] = []
    with pytest.raises(ManifestIntegrityError, match="R-310-204"):
        await service.resolve(PID, "B-01")


@pytest.mark.asyncio
async def test_verification_can_be_waived_for_a_deliberate_read(
    service: BaselineService, world: World
) -> None:
    """Off only on request: the default is to check."""
    await _take(service)
    world.objects[ARCH][0] = _public(
        "AD-100", ARCH, 30, "Rewritten.", ReviewState.AUTO_ACCEPTED
    )
    resolved = await service.resolve(PID, "B-01", verify=False)
    assert any(item.entry.object_id == "AD-100" for item in resolved)
