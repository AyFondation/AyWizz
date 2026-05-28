# =============================================================================
# File: test_import.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_import.py
# Description: Integration tests for the C5 corpus import endpoint
#              (R-300-080..083). Covers:
#                - Happy path: batch of 2 documents imported, response
#                  carries the exact list of slugs + entity IDs.
#                - `on_conflict=fail`: second import of same slug returns
#                  409 and SHALL NOT mutate the existing corpus.
#                - `on_conflict=replace`: existing document is bumped to
#                  the new version, entities rewritten.
#                - Validation failure: one malformed frontmatter in the
#                  batch returns 422 with the full error list, no writes.
#                - Role gate: a user without project_editor SHALL be 403.
#                - `format=reqif`: returns 501 until v2.
#
# @relation validates:R-300-080
# @relation validates:R-300-081
# @relation validates:R-300-082
# @relation validates:R-300-083
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.integration

_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_editor,project_owner",
}


def _doc(slug: str, entity_id: str, status: str = "draft") -> dict[str, str]:
    content = f"""---
document: {slug}
version: 1
path: projects/demo/requirements/{slug}.md
language: en
status: draft
---

# Imported spec {slug}

#### {entity_id}

```yaml
id: {entity_id}
version: 1
status: {status}
category: functional
```

Imported entity body.
"""
    return {"slug": slug, "content": content}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://c5"
    )


@pytest.mark.asyncio
async def test_import_batch_happy_path(c5_app: FastAPI) -> None:
    payload = {
        "documents": [
            _doc("800-SPEC-ONE", "R-800-001"),
            _doc("800-SPEC-TWO", "R-800-002"),
        ]
    }
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json=payload,
            headers=_HEADERS,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["imported_documents"] == ["800-SPEC-ONE", "800-SPEC-TWO"]
    assert body["imported_entities"] == ["R-800-001", "R-800-002"]
    assert body["summary"] == {"documents": 2, "entities": 2}


@pytest.mark.asyncio
async def test_import_fail_on_existing_slug(c5_app: FastAPI) -> None:
    """Default on_conflict=fail: a re-import of an already-present slug
    returns 409 and SHALL NOT mutate the corpus (R-300-081 + R-300-083)."""
    first = {"documents": [_doc("800-SPEC-ONE", "R-800-001", status="approved")]}
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json=first,
            headers=_HEADERS,
        )
        assert resp.status_code == 201

        conflict = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json=first,
            headers=_HEADERS,
        )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["conflicts"] == ["800-SPEC-ONE"]
    # The pre-existing entity SHALL stay at status=approved.
    async with _client(c5_app) as client:
        entity = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-800-001",
            headers=_HEADERS,
        )
    assert entity.status_code == 200
    assert entity.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_import_replace_overwrites_existing(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        first = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json={"documents": [_doc("800-SPEC-ONE", "R-800-001", status="draft")]},
            headers=_HEADERS,
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md", "on_conflict": "replace"},
            json={
                "documents": [_doc("800-SPEC-ONE", "R-800-001", status="approved")],
                "on_conflict": "replace",
            },
            headers=_HEADERS,
        )
    assert second.status_code == 201, second.text
    # After replace, the entity reflects the new body.
    async with _client(c5_app) as client:
        entity = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-800-001",
            headers=_HEADERS,
        )
    assert entity.status_code == 200
    assert entity.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_import_validation_errors_abort_batch(c5_app: FastAPI) -> None:
    """One bad frontmatter in a batch SHALL abort the whole request and
    return a structured validation error (R-300-082)."""
    payload = {
        "documents": [
            _doc("800-SPEC-ONE", "R-800-001"),
            {"slug": "800-SPEC-BROKEN", "content": "no frontmatter at all"},
        ]
    }
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json=payload,
            headers=_HEADERS,
        )
    assert resp.status_code == 422, resp.text
    errors = resp.json()["detail"]["validation_errors"]
    assert any("800-SPEC-BROKEN" in e for e in errors)

    # Neither document SHALL have landed (atomic pre-check).
    async with _client(c5_app) as client:
        get_good = await client.get(
            "/api/v1/projects/demo/requirements/documents/800-SPEC-ONE",
            headers=_HEADERS,
        )
    assert get_good.status_code == 404, (
        "Atomic guarantee broken: valid doc landed despite batch failure"
    )


@pytest.mark.asyncio
async def test_import_slug_mismatch_rejected(c5_app: FastAPI) -> None:
    """Frontmatter `document:` SHALL match the payload `slug`."""
    mismatch = _doc("800-SPEC-ONE", "R-800-001")
    # Force a mismatch by rewriting the content's `document:` line.
    mismatch["content"] = mismatch["content"].replace(
        "document: 800-SPEC-ONE", "document: 800-SPEC-WRONG"
    )
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json={"documents": [mismatch]},
            headers=_HEADERS,
        )
    assert resp.status_code == 422
    errors = resp.json()["detail"]["validation_errors"]
    assert any("does not match payload slug" in e for e in errors)


@pytest.mark.asyncio
async def test_import_requires_editor_role(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "md"},
            json={"documents": [_doc("800-SPEC-ONE", "R-800-001")]},
            headers={"X-User-Id": "bob", "X-User-Roles": "viewer"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_import_reqif_returns_501(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/import",
            params={"format": "reqif"},
            json={"documents": [_doc("800-SPEC-ONE", "R-800-001")]},
            headers=_HEADERS,
        )
    assert resp.status_code == 501
    assert "reqif" in resp.json()["detail"].lower()
