# =============================================================================
# File: test_object_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_object_http_verified.py
# Description: HTTP-level integration tests for the object surface, against
#              real MinIO + ArangoDB through the FastAPI app.
#
#              The service tier already proves the domain rules; this tier
#              proves the TRANSPORT decisions, which are contract and which
#              no lower tier can establish:
#                - a stale expected_version surfaces as 409 carrying both
#                  version numbers, so a UI can offer a diff (R-310-192);
#                - a foreign lease surfaces as 423 naming its holder, so the
#                  UI can say who to ask rather than "forbidden" (R-310-193);
#                - the role gates actually bind on the wire.
#
# @relation validates:R-310-192
# @relation validates:R-310-193
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c5_requirements.objects.locks import LockManager
from ay_platform_core.c5_requirements.objects.repository import ObjectRepository
from ay_platform_core.c5_requirements.objects.router import router as objects_router
from ay_platform_core.c5_requirements.objects.service import ObjectService
from ay_platform_core.c5_requirements.objects.storage import ObjectStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage
from tests.fixtures.containers import ArangoEndpoint, cleanup_arango_database

pytestmark = pytest.mark.integration

PID = "adas"
CONTAINER = "030-ARCH"

# Full literals, not f-strings: the functional-coverage coherence check reads
# test sources for the paths they exercise, and a composed URL makes real
# coverage invisible to it. Identifiers are kept short so each literal fits
# the line budget — the catalog matches these segments as placeholders, so
# their value is free.
BASE = "/api/v1/projects/adas/containers/030-ARCH/objects"
OBJ = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120"
OBJ_VERSIONS = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120/versions"
OBJ_VERSION_1 = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120/versions/1"
OBJ_DRAFT = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120/draft"
OBJ_REVIEW = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120/review"
OBJ_LOCK = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120/lock"
OBJ_LOCK_FORCE = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-1120/lock/force"
OBJ_MISSING = "/api/v1/projects/adas/containers/030-ARCH/objects/OBJ-9999"

_EDITOR = {"X-User-Id": "o.mathieu", "X-User-Roles": "project_editor"}
_OWNER = {"X-User-Id": "l.perrin", "X-User-Roles": "project_owner"}
_VIEWER = {"X-User-Id": "m.roche", "X-User-Roles": "project_viewer"}
_AGENT = {"X-User-Id": "agent:architect", "X-User-Roles": "project_editor"}


@pytest.fixture
def object_app(
    c5_storage: RequirementsStorage, arango_container: ArangoEndpoint
) -> Iterator[FastAPI]:
    db_name = f"c5_http_{uuid.uuid4().hex[:8]}"
    client = ArangoClient(hosts=arango_container.url)
    sys_db = client.db("_system", username="root", password=arango_container.password)
    sys_db.create_database(db_name)
    try:
        db = client.db(db_name, username="root", password=arango_container.password)
        repo = ObjectRepository(db)
        repo._ensure_collections_sync()
        locks = LockManager(db, lease_seconds=900)
        locks._ensure_collections_sync()
        app = FastAPI()
        app.include_router(objects_router)
        app.state.object_service = ObjectService(ObjectStorage(c5_storage), repo, locks)
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest.fixture
async def client(object_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=object_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def _create(client: httpx.AsyncClient, object_id: str = "OBJ-1120") -> None:
    response = await client.post(
        BASE,
        json={
            "object_id": object_id,
            "type": "paragraph",
            "body": "Ramp limited to 120 Nm/s.",
            "ordinal": 1,
        },
        headers=_AGENT,
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_create_then_read(client: httpx.AsyncClient) -> None:
    await _create(client)
    response = await client.get(OBJ, headers=_VIEWER)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["review_state"] == "proposed"
    assert body["has_working_draft"] is False
    assert body["lock"] is None


@pytest.mark.asyncio
async def test_anonymous_calls_are_refused(client: httpx.AsyncClient) -> None:
    assert (await client.get(BASE)).status_code == 401
    assert (await client.post(BASE, json={})).status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_write(client: httpx.AsyncClient) -> None:
    response = await client.post(
        BASE,
        json={
            "object_id": "OBJ-9",
            "type": "paragraph",
            "body": "x",
            "ordinal": 1,
        },
        headers=_VIEWER,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_read(client: httpx.AsyncClient) -> None:
    await _create(client)
    assert (await client.get(BASE, headers=_VIEWER)).status_code == 200


@pytest.mark.asyncio
async def test_stale_version_is_409_with_both_numbers(
    client: httpx.AsyncClient,
) -> None:
    """R-310-192 — the payload must let a UI offer a diff, not just fail."""
    await _create(client)
    accepted = await client.post(
        OBJ_REVIEW,
        json={"decision": "accept", "expected_version": 1},
        headers=_EDITOR,
    )
    assert accepted.status_code == 200

    stale = await client.put(
        OBJ_DRAFT,
        json={
            "type": "paragraph",
            "body": "late rework",
            "negotiation_id": "NEG-1",
            "base_version": 1,
        },
        headers=_EDITOR,
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["expected_version"] == 1
    assert detail["actual_version"] == 2


@pytest.mark.asyncio
async def test_foreign_lease_is_423_naming_the_holder(
    client: httpx.AsyncClient,
) -> None:
    """R-310-193 — "forbidden" would hide who to ask; 423 names the holder."""
    await _create(client)
    taken = await client.post(
        OBJ_LOCK, json={"holder_kind": "agent"}, headers=_AGENT
    )
    assert taken.status_code == 200

    blocked = await client.put(
        OBJ_DRAFT,
        json={
            "type": "paragraph",
            "body": "mine",
            "negotiation_id": "NEG-2",
            "base_version": 1,
        },
        headers=_EDITOR,
    )
    assert blocked.status_code == 423
    detail = blocked.json()["detail"]
    assert detail["holder"] == "agent:architect"
    assert detail["holder_kind"] == "agent"


@pytest.mark.asyncio
async def test_negotiation_then_review_over_http(client: httpx.AsyncClient) -> None:
    await _create(client)
    for body in ("first", "second", "third"):
        response = await client.put(
            OBJ_DRAFT,
            json={
                "type": "paragraph",
                "body": body,
                "negotiation_id": "NEG-3",
                "base_version": 1,
            },
            headers=_AGENT,
        )
        assert response.status_code == 200

    versions = await client.get(OBJ_VERSIONS, headers=_VIEWER)
    assert versions.json()["versions"] == [1]

    reviewed = await client.post(
        OBJ_REVIEW,
        json={"decision": "accept", "expected_version": 1},
        headers=_EDITOR,
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["body"] == "third"

    versions = await client.get(OBJ_VERSIONS, headers=_VIEWER)
    assert versions.json()["versions"] == [1, 2]


@pytest.mark.asyncio
async def test_confirm_unchanged_requires_a_justification(
    client: httpx.AsyncClient,
) -> None:
    await _create(client)
    response = await client.post(
        OBJ_REVIEW,
        json={"decision": "confirm-unchanged", "expected_version": 1},
        headers=_EDITOR,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_editor_cannot_break_a_foreign_lease(
    client: httpx.AsyncClient,
) -> None:
    await _create(client)
    await client.post(
        OBJ_LOCK, json={"holder_kind": "agent"}, headers=_AGENT
    )
    refused = await client.delete(OBJ_LOCK_FORCE, headers=_EDITOR)
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_break_a_foreign_lease(client: httpx.AsyncClient) -> None:
    await _create(client)
    await client.post(
        OBJ_LOCK, json={"holder_kind": "agent"}, headers=_AGENT
    )
    broken = await client.delete(OBJ_LOCK_FORCE, headers=_OWNER)
    assert broken.status_code == 204

    freed = await client.post(
        OBJ_LOCK, json={"holder_kind": "human"}, headers=_EDITOR
    )
    assert freed.status_code == 200


@pytest.mark.asyncio
async def test_retained_version_is_readable_over_http(
    client: httpx.AsyncClient,
) -> None:
    """R-310-202 — the retained chain is served, not merely stored."""
    await _create(client)
    await client.put(
        OBJ_DRAFT,
        json={
            "type": "paragraph",
            "body": "reworked",
            "negotiation_id": "NEG-4",
            "base_version": 1,
        },
        headers=_AGENT,
    )
    await client.post(
        OBJ_REVIEW,
        json={"decision": "accept", "expected_version": 1},
        headers=_EDITOR,
    )

    first = await client.get(OBJ_VERSION_1, headers=_VIEWER)
    assert first.status_code == 200
    assert first.json()["body"] == "Ramp limited to 120 Nm/s."


@pytest.mark.asyncio
async def test_draft_is_readable_over_http(client: httpx.AsyncClient) -> None:
    await _create(client)
    assert (await client.get(OBJ_DRAFT, headers=_VIEWER)).json() is None

    await client.put(
        OBJ_DRAFT,
        json={
            "type": "paragraph",
            "body": "in progress",
            "negotiation_id": "NEG-5",
            "base_version": 1,
        },
        headers=_AGENT,
    )
    live = await client.get(OBJ_DRAFT, headers=_VIEWER)
    assert live.status_code == 200
    assert live.json()["body"] == "in progress"
    assert live.json()["iteration"] == 1


@pytest.mark.asyncio
async def test_holder_releases_its_own_lease(client: httpx.AsyncClient) -> None:
    await _create(client)
    await client.post(OBJ_LOCK, json={"holder_kind": "human"}, headers=_EDITOR)
    released = await client.delete(OBJ_LOCK, headers=_EDITOR)
    assert released.status_code == 204
    retaken = await client.post(
        OBJ_LOCK, json={"holder_kind": "agent"}, headers=_AGENT
    )
    assert retaken.status_code == 200


@pytest.mark.asyncio
async def test_unknown_object_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(OBJ_MISSING, headers=_VIEWER)).status_code == 404


@pytest.mark.asyncio
async def test_malformed_container_slug_is_400(client: httpx.AsyncClient) -> None:
    """A slug that reaches the handler and would escape the namespace is 400.

    Single-segment on purpose: an encoded separator is decoded before routing
    and never reaches this code (see the test below), so asserting 400 on one
    would test Starlette, not the path guard.
    """
    for bad in (".hidden", "-leading", "bad slug"):
        response = await client.get(
            f"/api/v1/projects/{PID}/containers/{bad}/objects", headers=_VIEWER
        )
        assert response.status_code == 400, bad


@pytest.mark.asyncio
async def test_encoded_separator_never_resolves_a_container(
    client: httpx.AsyncClient,
) -> None:
    """The escape attempt must not succeed; which layer refuses it is moot."""
    response = await client.get(
        f"/api/v1/projects/{PID}/containers/..%2Fescape/objects", headers=_VIEWER
    )
    assert response.status_code in {400, 404}
    assert response.status_code != 200


@pytest.mark.asyncio
async def test_figure_without_notation_is_422(client: httpx.AsyncClient) -> None:
    """R-310-008 is a model invariant, so it binds at the boundary too."""
    response = await client.post(
        BASE,
        json={"object_id": "OBJ-77", "type": "figure", "ordinal": 1},
        headers=_AGENT,
    )
    assert response.status_code == 422
