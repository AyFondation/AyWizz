# =============================================================================
# File: test_intake_http_verified.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_intake_http_verified.py
# Description: HTTP-level integration tests for the intake surface, against
#              real MinIO with real uploaded files.
#
#              What only the boundary can establish:
#                - a real multipart upload of a real DOCX is ingested, which
#                  no lower tier exercises;
#                - verifying a degraded extraction is OWNER-gated, because
#                  splitting, allocation and coverage all rest on that
#                  assertion (R-310-061);
#                - the split response carries the rework owed to the issuer,
#                  so a client cannot be shown one without the other
#                  (R-310-094);
#                - a lossy split is refused with the LOST TEXT quoted
#                  (R-310-092).
#
#              URLs are full literals for the functional-coverage check.
#
# @relation validates:R-310-060
# @relation validates:R-310-061
# @relation validates:R-310-063
# @relation validates:R-310-092
# @relation validates:R-310-093
# @relation validates:R-310-094
# =============================================================================

from __future__ import annotations

import io
from collections.abc import AsyncIterator, Iterator
from typing import NamedTuple

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c5_requirements.intake.models import ATOMICITY_CRITERION
from ay_platform_core.c5_requirements.intake.router import router as intake_router
from ay_platform_core.c5_requirements.intake.service import IntakeService
from ay_platform_core.c5_requirements.intake.storage import IntakeStorage
from ay_platform_core.c5_requirements.storage.minio_storage import RequirementsStorage

pytestmark = pytest.mark.integration

DROP = "/api/v1/projects/adas/intake/drops/W14"
VERIFY = "/api/v1/projects/adas/intake/drops/W14/verify"
REQUIREMENTS = "/api/v1/projects/adas/intake/drops/W14/requirements"
REQ_ONE = "/api/v1/projects/adas/intake/drops/W14/requirements/W14-001"
FINDINGS = "/api/v1/projects/adas/intake/drops/W14/requirements/W14-001/findings"
SPLIT = "/api/v1/projects/adas/intake/drops/W14/requirements/W14-001/split"
FRAGMENTS = "/api/v1/projects/adas/intake/drops/W14/requirements/W14-001/fragments"
REWORK = "/api/v1/projects/adas/intake/drops/W14/rework"

_EDITOR = {"X-User-Id": "agent:req-analyst", "X-User-Roles": "project_editor"}
_OWNER = {"X-User-Id": "o.mathieu", "X-User-Roles": "project_owner"}
_VIEWER = {"X-User-Id": "l.perrin", "X-User-Roles": "project_viewer"}

_AGGLOMERATED = (
    "On brake pedal release the system shall reduce deceleration to zero "
    "within 250 ms, and limit the torque ramp to 140 Nm/s."
)
_SPLIT_AT = _AGGLOMERATED.index(", and limit")
_DETAIL = "Two independent obligations in one sentence: timing and ramp."


@pytest.fixture
def intake_app(c5_storage: RequirementsStorage) -> Iterator[FastAPI]:
    app = FastAPI()
    app.include_router(intake_router)
    app.state.intake_service = IntakeService(IntakeStorage(c5_storage))
    yield app


@pytest.fixture
async def client(intake_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=intake_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


class _Upload(NamedTuple):
    """A multipart ingest body, typed as httpx expects it.

    A plain `dict[str, object]` erased both field types and forced a
    blanket `type: ignore` on every call site, which would have hidden a
    genuinely malformed upload. Named fields keep mypy useful here.
    """

    files: dict[str, tuple[str, bytes, str]]
    data: dict[str, str]


def _md_upload(*, needed_ocr: bool = False) -> _Upload:
    return _Upload(
        files={"file": ("W14.md", _AGGLOMERATED.encode(), "text/markdown")},
        data={"source_format": "md", "needed_ocr": str(needed_ocr).lower()},
    )


async def _ingest(
    client: httpx.AsyncClient, *, needed_ocr: bool = False
) -> httpx.Response:
    upload = _md_upload(needed_ocr=needed_ocr)
    return await client.post(
        DROP, files=upload.files, data=upload.data, headers=_EDITOR
    )


async def _record_atomicity(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        FINDINGS,
        json={"criterion_id": ATOMICITY_CRITERION, "detail": _DETAIL},
        headers=_EDITOR,
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_is_refused(client: httpx.AsyncClient) -> None:
    upload = _md_upload()
    response = await client.post(
        DROP, files=upload.files, data=upload.data
    )
    assert response.status_code == 401
    assert (await client.get(REQUIREMENTS)).status_code == 401


@pytest.mark.asyncio
async def test_ingest_is_editor_gated(client: httpx.AsyncClient) -> None:
    upload = _md_upload()
    refused = await client.post(
        DROP, files=upload.files, data=upload.data, headers=_VIEWER
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_verification_is_owner_gated(client: httpx.AsyncClient) -> None:
    """R-310-061 — splitting, allocation and coverage all rest on it."""
    await _ingest(client)
    assert (await client.post(VERIFY, headers=_EDITOR)).status_code == 403
    assert (await client.post(VERIFY, headers=_OWNER)).status_code == 200


@pytest.mark.asyncio
async def test_reads_are_open_to_any_member(client: httpx.AsyncClient) -> None:
    await _ingest(client)
    assert (await client.get(REQUIREMENTS, headers=_VIEWER)).status_code == 200
    assert (await client.get(REQ_ONE, headers=_VIEWER)).status_code == 200


# ---------------------------------------------------------------------------
# Ingest over the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_markdown_upload_is_ingested(client: httpx.AsyncClient) -> None:
    response = await _ingest(client)
    assert response.status_code == 201
    body = response.json()
    assert body["requirement_ids"] == ["W14-001"]
    assert body["source_class"] == "structured"
    assert body["needs_verification"] is False


@pytest.mark.asyncio
async def test_a_real_docx_upload_is_ingested(client: httpx.AsyncClient) -> None:
    """A real multipart upload of a real file — no lower tier does this."""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph(_AGGLOMERATED)
    buffer = io.BytesIO()
    document.save(buffer)

    response = await client.post(
        DROP,
        files={
            "file": (
                "W14.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
            )
        },
        data={"source_format": "docx"},
        headers=_EDITOR,
    )
    assert response.status_code == 201
    assert response.json()["source_class"] == "textual"


@pytest.mark.asyncio
async def test_a_file_that_is_not_what_it_claims_is_422(
    client: httpx.AsyncClient,
) -> None:
    # 422, not 400: the request was well-formed, the FILE is not.
    response = await client.post(
        DROP,
        files={"file": ("W14.docx", b"this is not a docx", "application/octet-stream")},
        data={"source_format": "docx"},
        headers=_EDITOR,
    )
    assert response.status_code == 422
    assert "not a readable DOCX" in response.json()["detail"]


@pytest.mark.asyncio
async def test_an_ocr_drop_reports_that_it_needs_verification(
    client: httpx.AsyncClient,
) -> None:
    response = await _ingest(client, needed_ocr=True)
    assert response.json()["source_class"] == "degraded"
    assert response.json()["needs_verification"] is True


@pytest.mark.asyncio
async def test_a_stored_requirement_is_served_with_its_anchor(
    client: httpx.AsyncClient,
) -> None:
    await _ingest(client)
    body = (await client.get(REQ_ONE, headers=_VIEWER)).json()
    assert body["text"] == _AGGLOMERATED
    assert body["anchor"]["source_file"] == "W14.md"
    assert body["anchor"]["location"] == "line 1"


@pytest.mark.asyncio
async def test_an_unknown_requirement_is_404(client: httpx.AsyncClient) -> None:
    await _ingest(client)
    missing = "/api/v1/projects/adas/intake/drops/W14/requirements/W14-999"
    assert (await client.get(missing, headers=_VIEWER)).status_code == 404


# ---------------------------------------------------------------------------
# Findings — R-310-063
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finding_is_recorded_and_listed(client: httpx.AsyncClient) -> None:
    await _ingest(client)
    created = await _record_atomicity(client)
    assert created.status_code == 201
    assert created.json()["findings"][0]["criterion_id"] == ATOMICITY_CRITERION

    listed = await client.get(FINDINGS, headers=_VIEWER)
    assert len(listed.json()["findings"]) == 1


@pytest.mark.asyncio
async def test_an_uncited_finding_is_422(client: httpx.AsyncClient) -> None:
    """R-310-063 — an LLM's supply of plausible objections is unlimited."""
    await _ingest(client)
    response = await client.post(
        FINDINGS,
        json={"criterion_id": "quality-problem", "detail": _DETAIL},
        headers=_EDITOR,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Splitting — R-310-092 / R-310-093 / R-310-094
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_split_without_a_recorded_finding_is_409(
    client: httpx.AsyncClient,
) -> None:
    await _ingest(client)
    response = await client.post(
        SPLIT,
        json={
            "spans": [
                {"start": 0, "end": _SPLIT_AT},
                {"start": _SPLIT_AT, "end": len(_AGGLOMERATED)},
            ]
        },
        headers=_EDITOR,
    )
    assert response.status_code == 409
    assert "not a free action" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_justified_split_returns_the_rework_too(
    client: httpx.AsyncClient,
) -> None:
    """R-310-094 — a client cannot be shown one without the other."""
    await _ingest(client)
    await _record_atomicity(client)
    response = await client.post(
        SPLIT,
        json={
            "spans": [
                {"start": 0, "end": _SPLIT_AT},
                {"start": _SPLIT_AT, "end": len(_AGGLOMERATED)},
            ]
        },
        headers=_EDITOR,
    )
    assert response.status_code == 201
    body = response.json()
    assert [f["fragment_id"] for f in body["proposal"]["fragments"]] == [
        "W14-001/1", "W14-001/2",
    ]
    assert body["rework"]["criterion_id"] == ATOMICITY_CRITERION
    assert "please issue them separately" in body["rework"]["detail"]


@pytest.mark.asyncio
async def test_a_lossy_split_is_422_quoting_the_lost_text(
    client: httpx.AsyncClient,
) -> None:
    """R-310-092 at the boundary — the author must see WHAT was dropped."""
    await _ingest(client)
    await _record_atomicity(client)
    response = await client.post(
        SPLIT,
        json={
            "spans": [
                {"start": 0, "end": 20},
                {"start": _SPLIT_AT, "end": len(_AGGLOMERATED)},
            ]
        },
        headers=_EDITOR,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "does not tile the source" in detail
    assert "deceleration" in detail


@pytest.mark.asyncio
async def test_a_single_span_split_is_422(client: httpx.AsyncClient) -> None:
    await _ingest(client)
    await _record_atomicity(client)
    response = await client.post(
        SPLIT, json={"spans": [{"start": 0, "end": 10}]}, headers=_EDITOR
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_degraded_drop_cannot_be_split_until_verified(
    client: httpx.AsyncClient,
) -> None:
    await _ingest(client, needed_ocr=True)
    await _record_atomicity(client)
    spans = {
        "spans": [
            {"start": 0, "end": _SPLIT_AT},
            {"start": _SPLIT_AT, "end": len(_AGGLOMERATED)},
        ]
    }

    blocked = await client.post(SPLIT, json=spans, headers=_EDITOR)
    assert blocked.status_code == 409
    assert "degraded source" in blocked.json()["detail"]

    assert (await client.post(VERIFY, headers=_OWNER)).json()["cleared"] == [
        "W14-001"
    ]
    assert (await client.post(SPLIT, json=spans, headers=_EDITOR)).status_code == 201


@pytest.mark.asyncio
async def test_fragments_and_rework_are_served(client: httpx.AsyncClient) -> None:
    await _ingest(client)
    await _record_atomicity(client)
    await client.post(
        SPLIT,
        json={
            "spans": [
                {"start": 0, "end": _SPLIT_AT},
                {"start": _SPLIT_AT, "end": len(_AGGLOMERATED)},
            ]
        },
        headers=_EDITOR,
    )

    fragments = (await client.get(FRAGMENTS, headers=_VIEWER)).json()["fragments"]
    assert len(fragments) == 2
    assert fragments[1]["statement"].startswith("and limit the torque")

    owed = (await client.get(REWORK, headers=_VIEWER)).json()["requests"]
    assert [r["requirement_id"] for r in owed] == ["W14-001"]


@pytest.mark.asyncio
async def test_an_unsplit_requirement_serves_no_fragments(
    client: httpx.AsyncClient,
) -> None:
    await _ingest(client)
    assert (await client.get(FRAGMENTS, headers=_VIEWER)).json()["fragments"] == []
