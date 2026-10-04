# =============================================================================
# File: test_coverage_speculative_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_coverage_speculative_verified.py
# Description: The speculative marking against a real graph — R-310-177 v2.
#
#              What this tier establishes that the model test cannot: the
#              marking is DERIVED on every call. Accepting the upstream
#              clears it with no write to the covering object — which is the
#              property a stored flag would lose, and the reason
#              `suspect_links` is computed too.
#
# @relation validates:R-310-177
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.coverage.models import CoverageLink
from ay_platform_core.c5_requirements.coverage.repository import CoverageRepository
from ay_platform_core.c5_requirements.coverage.router import (
    router as coverage_router,
)
from ay_platform_core.c5_requirements.coverage.service import CoverageService
from ay_platform_core.c5_requirements.objects.models import ReviewState
from ay_platform_core.c5_requirements.process.repository import ProcessRepository
from ay_platform_core.c5_requirements.process.service import ProcessService
from ay_platform_core.c5_requirements.process.storage import ProcessStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
PID = "adas"
ARCH = "030-ARCH"
SPECULATIVE = "/api/v1/projects/adas/coverage/speculative"
_VIEWER = {"X-User-Id": "v.iewer", "X-User-Roles": "project_viewer"}


class Graph:
    """The review state and version of each object, driven by the test."""

    def __init__(self) -> None:
        self.states: dict[str, ReviewState] = {}
        self.versions: dict[str, int] = {}

    async def state_of(self, project_id: str, object_id: str) -> ReviewState | None:
        return self.states.get(object_id)

    async def version_of(self, project_id: str, object_id: str) -> int | None:
        return self.versions.get(object_id)


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[CoverageService, CoverageRepository, Graph, FastAPI]]:
    db_name = f"c5_spec_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        process_repo = ProcessRepository(db)
        process_repo._ensure_collections_sync()
        process = ProcessService(ProcessStorage(c5_storage), process_repo)
        coverage_repo = CoverageRepository(db)
        coverage_repo._ensure_collections_sync()
        graph = Graph()
        service = CoverageService(
            coverage_repo, process, graph.version_of, graph.state_of
        )
        app = FastAPI()
        app.include_router(coverage_router)
        app.state.coverage_service = service
        yield service, coverage_repo, graph, app
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
def service(
    wired: tuple[CoverageService, CoverageRepository, Graph, FastAPI],
) -> CoverageService:
    return wired[0]


@pytest.fixture
def links(
    wired: tuple[CoverageService, CoverageRepository, Graph, FastAPI],
) -> CoverageRepository:
    return wired[1]


@pytest.fixture
def graph(
    wired: tuple[CoverageService, CoverageRepository, Graph, FastAPI],
) -> Graph:
    return wired[2]


@pytest.fixture
async def client(
    wired: tuple[CoverageService, CoverageRepository, Graph, FastAPI],
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=wired[3])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _link(
    links: CoverageRepository,
    object_id: str,
    target_id: str,
    pinned: int = 1,
    container: str = ARCH,
) -> None:
    await links.put_coverage(
        CoverageLink(
            project_id=PID,
            object_id=object_id,
            container=container,
            target_id=target_id,
            pinned_version=pinned,
            actor="o.mathieu",
            at=NOW,
        )
    )


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_object_on_an_accepted_upstream_is_not_speculative(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010")
    graph.states["FD-010"] = ReviewState.ACCEPTED
    assert await service.speculative_objects(PID) == ()


@pytest.mark.asyncio
async def test_an_auto_accepted_upstream_is_also_an_accepted_foundation(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    """R-310-007 keeps them distinct for counting; both are acceptance."""
    await _link(links, "AD-100", "FD-010")
    graph.states["FD-010"] = ReviewState.AUTO_ACCEPTED
    assert await service.speculative_objects(PID) == ()


@pytest.mark.asyncio
async def test_an_object_on_a_proposed_upstream_is_speculative(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010")
    graph.states["FD-010"] = ReviewState.PROPOSED

    markings = await service.speculative_objects(PID)
    assert len(markings) == 1
    assert markings[0].object_id == "AD-100"
    assert markings[0].unaccepted_targets == ("FD-010",)
    assert markings[0].must_become_stale is False


@pytest.mark.asyncio
async def test_accepting_the_upstream_clears_the_marking_with_no_write(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    """The property a stored flag would lose.

    Nothing writes to AD-100 when FD-010 is accepted, so a column on AD-100
    would still say 'speculative' — the drift this design avoids.
    """
    await _link(links, "AD-100", "FD-010")
    graph.states["FD-010"] = ReviewState.PROPOSED
    assert len(await service.speculative_objects(PID)) == 1

    graph.states["FD-010"] = ReviewState.ACCEPTED
    assert await service.speculative_objects(PID) == ()


@pytest.mark.asyncio
async def test_an_unaccepted_upstream_that_advanced_must_become_stale(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010", pinned=2)
    graph.states["FD-010"] = ReviewState.PROPOSED
    graph.versions["FD-010"] = 5

    markings = await service.speculative_objects(PID)
    assert markings[0].must_become_stale is True
    assert markings[0].advanced_targets == ("FD-010",)
    assert await service.objects_to_stale(PID) == ("AD-100",)


@pytest.mark.asyncio
async def test_an_upstream_pinned_at_its_current_version_is_speculative_not_stale(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010", pinned=5)
    graph.states["FD-010"] = ReviewState.PROPOSED
    graph.versions["FD-010"] = 5

    markings = await service.speculative_objects(PID)
    assert markings[0].is_speculative is True
    assert markings[0].must_become_stale is False
    assert await service.objects_to_stale(PID) == ()


@pytest.mark.asyncio
async def test_an_object_with_one_accepted_and_one_proposed_upstream_is_speculative(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    """Partially founded is not founded."""
    await _link(links, "AD-100", "FD-010")
    await _link(links, "AD-100", "FD-011")
    graph.states["FD-010"] = ReviewState.ACCEPTED
    graph.states["FD-011"] = ReviewState.PROPOSED

    markings = await service.speculative_objects(PID)
    assert markings[0].unaccepted_targets == ("FD-011",)


@pytest.mark.asyncio
async def test_a_target_outside_the_object_corpus_is_not_counted_unaccepted(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    """A supplied requirement has no review state; it is not 'unaccepted'.

    Treating unknown as unaccepted would mark every object answering a
    customer requirement speculative, which is every object in the first
    container — loud and useless.
    """
    await _link(links, "FD-010", "CUST-001")
    assert await service.speculative_objects(PID) == ()


@pytest.mark.asyncio
async def test_the_listing_can_be_narrowed_to_one_container(
    service: CoverageService, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010", container=ARCH)
    await _link(links, "TD-900", "AD-100", container="050-TECH")
    graph.states["FD-010"] = ReviewState.PROPOSED
    graph.states["AD-100"] = ReviewState.PROPOSED

    assert len(await service.speculative_objects(PID)) == 2
    narrowed = await service.speculative_objects(PID, container=ARCH)
    assert [marking.object_id for marking in narrowed] == ["AD-100"]


@pytest.mark.asyncio
async def test_without_a_state_lookup_nothing_is_reported_speculative(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> None:
    """A misconfiguration must not mark the whole corpus speculative.

    Defaulting to "unknown" and then treating unknown as unaccepted is the
    loud-but-useless failure; reporting nothing is the quiet one, and it is
    visible because the workbench panel is empty.
    """
    db_name = f"c5_spec_none_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        process_repo = ProcessRepository(db)
        process_repo._ensure_collections_sync()
        coverage_repo = CoverageRepository(db)
        coverage_repo._ensure_collections_sync()
        graph = Graph()
        service = CoverageService(
            coverage_repo,
            ProcessService(ProcessStorage(c5_storage), process_repo),
            graph.version_of,
        )
        await _link(coverage_repo, "AD-100", "FD-010")
        assert await service.speculative_objects(PID) == ()
    finally:
        cleanup_arango_database(arango_container, db_name)


# ---------------------------------------------------------------------------
# The REST surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_cannot_read_the_speculative_list(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get(SPECULATIVE)).status_code == 401


@pytest.mark.asyncio
async def test_the_route_returns_the_markings_and_separates_the_stale_ones(
    client: httpx.AsyncClient, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010", pinned=1)
    await _link(links, "AD-101", "FD-011", pinned=1)
    graph.states["FD-010"] = ReviewState.PROPOSED
    graph.states["FD-011"] = ReviewState.PROPOSED
    graph.versions["FD-011"] = 4

    body = (await client.get(SPECULATIVE, headers=_VIEWER)).json()
    assert body["count"] == 2
    assert body["stale_count"] == 1
    stale = [m for m in body["markings"] if m["advanced_targets"]]
    assert stale[0]["object_id"] == "AD-101"


@pytest.mark.asyncio
async def test_the_route_accepts_a_container_filter(
    client: httpx.AsyncClient, links: CoverageRepository, graph: Graph
) -> None:
    await _link(links, "AD-100", "FD-010", container=ARCH)
    await _link(links, "TD-900", "AD-100", container="050-TECH")
    graph.states["FD-010"] = ReviewState.PROPOSED
    graph.states["AD-100"] = ReviewState.PROPOSED

    body = (
        await client.get(
            SPECULATIVE, params={"container": ARCH}, headers=_VIEWER
        )
    ).json()
    assert body["count"] == 1
    assert body["markings"][0]["object_id"] == "AD-100"


@pytest.mark.asyncio
async def test_an_empty_project_returns_an_empty_listing(
    client: httpx.AsyncClient,
) -> None:
    body = (await client.get(SPECULATIVE, headers=_VIEWER)).json()
    assert body["count"] == 0
    assert body["markings"] == []
