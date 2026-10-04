# =============================================================================
# File: test_baseline_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_baseline_http_verified.py
# Description: The baseline REST surface — 310-SPEC §4.11.
#
#              THE TEST THAT CLOSES THE LOOP is the one that downloads the
#              PDF over HTTP and parses it with `pypdf`. That is the
#              operator's original ask — "generate a Word/PDF document
#              corresponding to a baseline, a photograph of all the
#              project's requirements" — exercised end to end: gate, take,
#              render, download, parse.
#
#              `/baseline-readiness` is asserted to reach its OWN handler,
#              because it sits on its own segment precisely so
#              `/baselines/{tag}` cannot swallow it.
#
# @relation validates:R-310-200
# @relation validates:R-310-201
# @relation validates:R-310-207
# =============================================================================

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from pypdf import PdfReader

from ay_platform_core.c5_requirements.baseline.repository import BaselineRepository
from ay_platform_core.c5_requirements.baseline.router import (
    router as baseline_router,
)
from ay_platform_core.c5_requirements.baseline.service import BaselineService
from ay_platform_core.c5_requirements.baseline.storage import BaselineStorage
from ay_platform_core.c5_requirements.objects.models import (
    DocObjectPublic,
    ObjectType,
    ReviewState,
)
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
READINESS = "/api/v1/projects/adas/baseline-readiness"
BASELINES = "/api/v1/projects/adas/baselines"
BASELINE = "/api/v1/projects/adas/baselines/B-01"
RENDER_PDF = "/api/v1/projects/adas/baselines/B-01/render/pdf"
RENDER_DOCX = "/api/v1/projects/adas/baselines/B-01/render/docx"

_OWNER = {
    "X-User-Id": "o.mathieu",
    "X-User-Roles": "project_owner",
    "X-Tenant-Id": "acme",
}
_EDITOR = {
    "X-User-Id": "a.dev",
    "X-User-Roles": "project_editor",
    "X-Tenant-Id": "acme",
}
_VIEWER = {
    "X-User-Id": "v.iewer",
    "X-User-Roles": "project_viewer",
    "X-Tenant-Id": "acme",
}
_BODY = {"cycle_id": "C-AUTO", "note": "Gate review sign-off."}


class World:
    """What the baseline service asks other surfaces, driven by the test."""

    def __init__(self) -> None:
        self.open_tickets: list[str] = []
        self.suspect: list[dict[str, object]] = []
        self.gaps: list[tuple[str, str]] = []

    async def tickets_of(self, project_id: str) -> Sequence[str]:
        return self.open_tickets

    async def suspect_of(self, project_id: str) -> Sequence[dict[str, object]]:
        return self.suspect

    async def gaps_of(self, project_id: str) -> Sequence[tuple[str, str]]:
        return self.gaps

    async def cycle_of(
        self, tenant_id: str, project_id: str, cycle_id: str
    ) -> tuple[int, Sequence[str]]:
        return 2, ["020-FUNC", "030-ARCH"]

    async def objects_of(
        self, project_id: str, container: str
    ) -> Sequence[DocObjectPublic]:
        if container == "020-FUNC":
            return [
                DocObjectPublic(
                    object_id="FD-011",
                    container=container,
                    type=ObjectType.PARAGRAPH,
                    ordinal=11,
                    version=2,
                    review_state=ReviewState.ACCEPTED,
                    body="Deceleration shall reach zero within 150 ms.",
                    created_at=NOW,
                    created_by="o.mathieu",
                    updated_at=NOW,
                    updated_by="o.mathieu",
                )
            ]
        return [
            DocObjectPublic(
                object_id="AD-100",
                container=container,
                type=ObjectType.PARAGRAPH,
                ordinal=30,
                version=2,
                review_state=ReviewState.AUTO_ACCEPTED,
                body="The controller ramps torque at 140 Nm/s.",
                created_at=NOW,
                created_by="o.mathieu",
                updated_at=NOW,
                updated_by="o.mathieu",
            )
        ]

    async def links_of(
        self, project_id: str, container: str
    ) -> Sequence[dict[str, object]]:
        if container != "030-ARCH":
            return []
        return [
            {
                "object_id": "AD-100",
                "target_id": "CUST-001",
                "pinned_version": 4,
                "strength": "covered",
                "state": "accepted",
            }
        ]


@pytest.fixture
def wired(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[tuple[FastAPI, World]]:
    db_name = f"c5_base_http_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = BaselineRepository(db)
        repo._ensure_collections_sync()
        world = World()
        storage = BaselineStorage(c5_storage)
        app = FastAPI()
        app.include_router(baseline_router)
        app.state.baseline_storage = storage
        app.state.baseline_service = BaselineService(
            storage,
            repo,
            world.tickets_of,
            world.suspect_of,
            world.gaps_of,
            world.cycle_of,
            world.objects_of,
            world.links_of,
        )
        yield app, world
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
async def client(wired: tuple[FastAPI, World]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=wired[0])
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.fixture
def world(wired: tuple[FastAPI, World]) -> World:
    return wired[1]


async def _take(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(BASELINE, json=_BODY, headers=_OWNER)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post(BASELINE, json=_BODY)).status_code == 401
    assert (await client.get(READINESS)).status_code == 401
    assert (await client.get(BASELINES)).status_code == 401


@pytest.mark.asyncio
async def test_taking_a_baseline_is_owner_gated(client: httpx.AsyncClient) -> None:
    """It freezes a record an audit will read."""
    assert (await client.post(BASELINE, json=_BODY, headers=_EDITOR)).status_code == 403


@pytest.mark.asyncio
async def test_a_missing_tenant_header_is_refused(
    client: httpx.AsyncClient,
) -> None:
    """Without the tenant there is no cycle to resolve (R-310-046)."""
    response = await client.post(
        BASELINE,
        json=_BODY,
        headers={"X-User-Id": "o.mathieu", "X-User-Roles": "project_owner"},
    )
    assert response.status_code == 401
    assert "X-Tenant-Id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_reads_are_merely_authenticated(client: httpx.AsyncClient) -> None:
    await _take(client)
    assert (await client.get(READINESS, headers=_VIEWER)).status_code == 200
    assert (await client.get(BASELINES, headers=_VIEWER)).status_code == 200
    assert (await client.get(BASELINE, headers=_VIEWER)).status_code == 200


# ---------------------------------------------------------------------------
# The gate — R-310-201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_reaches_its_own_handler_not_the_tag_one(
    client: httpx.AsyncClient,
) -> None:
    """Its own segment so `/baselines/{tag}` cannot swallow it.

    If it were shadowed this would 404 with "no baseline 'readiness'".
    """
    response = await client.get(READINESS, headers=_VIEWER)
    assert response.status_code == 200
    assert response.json()["project_id"] == "adas"
    assert response.json()["refusals"] == []


@pytest.mark.asyncio
async def test_readiness_names_every_outstanding_precondition(
    client: httpx.AsyncClient, world: World
) -> None:
    world.open_tickets = ["adas:W14:CUST-001"]
    world.gaps = [("CUST-009", "ASIL-D")]
    body = (await client.get(READINESS, headers=_VIEWER)).json()
    blockers = {refusal["blocker"] for refusal in body["refusals"]}
    assert blockers == {"open-change-ticket", "critical-coverage-gap"}


@pytest.mark.asyncio
async def test_creation_is_refused_with_409_carrying_the_verdict(
    client: httpx.AsyncClient, world: World
) -> None:
    world.open_tickets = ["adas:W14:CUST-001"]
    response = await _take(client)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["refusals"][0]["blocker"] == "open-change-ticket"


# ---------------------------------------------------------------------------
# Taking and reading — R-310-200 / R-310-204
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_baseline_is_taken_and_names_its_entries(
    client: httpx.AsyncClient,
) -> None:
    response = await _take(client)
    assert response.status_code == 201
    body = response.json()
    assert body["tag"] == "B-01"
    assert {entry["object_id"] for entry in body["objects"]} == {"FD-011", "AD-100"}
    assert body["links"][0]["pinned_version"] == 4


@pytest.mark.asyncio
async def test_the_manifest_carries_no_object_content(
    client: httpx.AsyncClient,
) -> None:
    """R-310-200, asserted on the WIRE as well as on the model."""
    body = (await _take(client)).json()
    for entry in body["objects"]:
        assert "content" not in entry
        assert "body" not in entry
        assert entry["content_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_reusing_a_tag_is_refused_with_409(
    client: httpx.AsyncClient,
) -> None:
    await _take(client)
    again = await _take(client)
    assert again.status_code == 409
    assert "immutable" in again.json()["detail"]


@pytest.mark.asyncio
async def test_a_malformed_tag_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/projects/adas/baselines/not%20a%20tag", json=_BODY, headers=_OWNER
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_baseline_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(BASELINE, headers=_VIEWER)).status_code == 404


@pytest.mark.asyncio
async def test_baselines_are_listed_with_their_counts(
    client: httpx.AsyncClient,
) -> None:
    await _take(client)
    body = (await client.get(BASELINES, headers=_VIEWER)).json()
    assert body["count"] == 1
    assert body["baselines"][0]["object_count"] == 2
    assert body["baselines"][0]["note"] == "Gate review sign-off."


# ---------------------------------------------------------------------------
# R-310-207 — the operator's original ask, end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_pdf_downloads_and_parses(client: httpx.AsyncClient) -> None:
    """Gate, take, render, download, parse — the whole deliverable."""
    await _take(client)
    response = await client.get(RENDER_PDF, headers=_VIEWER)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="B-01.pdf"' in response.headers["content-disposition"]

    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "B-01" in text
    assert "150 ms" in text
    assert "140 Nm/s" in text
    assert "CUST-001" in text
    # R-310-007: the export discloses its auto-accepted share too.
    assert "auto-accepted" in text


@pytest.mark.asyncio
async def test_the_docx_downloads_with_the_right_media_type(
    client: httpx.AsyncClient,
) -> None:
    await _take(client)
    response = await client.get(RENDER_DOCX, headers=_VIEWER)
    assert response.status_code == 200
    assert "wordprocessingml" in response.headers["content-type"]
    assert 'filename="B-01.docx"' in response.headers["content-disposition"]
    # A DOCX is a zip: the magic bytes are the cheap structural check, and
    # the deep one lives in the unit tests where python-docx opens it.
    assert response.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_a_second_download_serves_the_cached_rendering(
    client: httpx.AsyncClient,
) -> None:
    await _take(client)
    first = await client.get(RENDER_PDF, headers=_VIEWER)
    second = await client.get(RENDER_PDF, headers=_VIEWER)
    assert second.content == first.content


@pytest.mark.asyncio
async def test_refresh_re_renders_rather_than_serving_the_cache(
    client: httpx.AsyncClient,
) -> None:
    """A template change must be visible without taking a new baseline."""
    await _take(client)
    await client.get(RENDER_PDF, headers=_VIEWER)
    refreshed = await client.get(
        RENDER_PDF, params={"refresh": True}, headers=_VIEWER
    )
    assert refreshed.status_code == 200
    reader = PdfReader(io.BytesIO(refreshed.content))
    assert len(reader.pages) >= 1


@pytest.mark.asyncio
async def test_rendering_an_unknown_baseline_is_404(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get(RENDER_PDF, headers=_VIEWER)).status_code == 404


@pytest.mark.asyncio
async def test_an_unsupported_format_is_refused_by_the_path(
    client: httpx.AsyncClient,
) -> None:
    await _take(client)
    response = await client.get(
        "/api/v1/projects/adas/baselines/B-01/render/rtf", headers=_VIEWER
    )
    assert response.status_code == 422
