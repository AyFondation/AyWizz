# =============================================================================
# File: test_crud_flow.py
# Version: 2
# Path: ay_platform_core/tests/integration/c5_requirements/test_crud_flow.py
# Description: Integration tests for the C5 write-through path: MinIO +
#              ArangoDB + NullPublisher. Exercises document creation,
#              entity read/update, optimistic locking, and tailoring
#              enforcement against real storage backends.
#              v2: 501 reindex stub test replaced by real v1.5 reindex
#              verification; import stub stays (deferred to v2).
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c5_requirements.events.null_publisher import NullPublisher

pytestmark = pytest.mark.integration

_ACTOR_HEADERS = {
    "X-User-Id": "alice",
    "X-User-Roles": "project_editor,project_owner",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


_DOC_BODY = """---
document: 300-SPEC-SAMPLE
version: 1
path: projects/demo/requirements/300-SPEC-SAMPLE.md
language: en
status: draft
---

# Sample Spec

#### R-300-001

```yaml
id: R-300-001
version: 1
status: draft
category: functional
```

The system SHALL greet the user on login.
"""


_TAILORED_DOC = """---
document: 300-SPEC-TAILORED
version: 1
path: projects/demo/requirements/300-SPEC-TAILORED.md
language: en
status: draft
---

# Tailored Spec

#### R-300-002

```yaml
id: R-300-002
version: 1
status: draft
category: functional
tailoring-of: R-100-001
override: true
```

### Tailoring rationale

This project needs a stricter greeting cadence because regulatory context
requires every session to log the greeting event.
"""


_PLATFORM_PARENT_DOC = """---
document: 100-SPEC-ARCHITECTURE
version: 1
path: platform/requirements/100-SPEC-ARCHITECTURE.md
language: en
status: draft
---

# Platform

#### R-100-001

```yaml
id: R-100-001
version: 1
status: approved
category: architecture
```

Platform-level greeting requirement.
"""


# ---------------------------------------------------------------------------
# Create / list / get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_document_and_fetch_entity(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers=_ACTOR_HEADERS,
        )
        assert resp.status_code == 201

        # PUT the full document body (the create endpoint only bootstraps the shell)
        resp = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            json={"content": _DOC_BODY},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-SAMPLE@v1"'},
        )
        assert resp.status_code == 200
        doc = resp.json()
        assert doc["slug"] == "300-SPEC-SAMPLE"
        assert doc["entity_count"] == 1

        # Entity is now visible
        resp = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-300-001",
            headers=_ACTOR_HEADERS,
        )
        assert resp.status_code == 200
        entity = resp.json()
        assert entity["entity_id"] == "R-300-001"
        assert entity["status"] == "draft"
        assert entity["category"] == "functional"


@pytest.mark.asyncio
async def test_list_entities_returns_new_entity(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers=_ACTOR_HEADERS,
        )
        await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            json={"content": _DOC_BODY},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-SAMPLE@v1"'},
        )
        resp = await client.get(
            "/api/v1/projects/demo/requirements/entities",
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 200
    ids = {e["entity_id"] for e in resp.json()["entities"]}
    assert "R-300-001" in ids


@pytest.mark.asyncio
async def test_get_document_returns_body(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers=_ACTOR_HEADERS,
        )
        await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            json={"content": _DOC_BODY},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-SAMPLE@v1"'},
        )
        resp = await client.get(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "R-300-001" in body["body"]


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_without_if_match_returns_428(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers=_ACTOR_HEADERS,
        )
        # Missing If-Match header
        resp = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            json={"content": _DOC_BODY},
            headers=_ACTOR_HEADERS,
        )
    assert resp.status_code == 428


@pytest.mark.asyncio
async def test_replace_with_stale_if_match_returns_412(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers=_ACTOR_HEADERS,
        )
        resp = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            json={"content": _DOC_BODY},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-SAMPLE@v999"'},
        )
    assert resp.status_code == 412
    assert "current_version" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Tailoring enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tailoring_rejected_when_platform_parent_missing(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-TAILORED"},
            headers=_ACTOR_HEADERS,
        )
        resp = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-TAILORED",
            json={"content": _TAILORED_DOC},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-TAILORED@v1"'},
        )
    assert resp.status_code == 422
    body = resp.json()
    detail = body["detail"]
    assert any("R-100-001" in issue["message"] for issue in detail["issues"])


@pytest.mark.asyncio
async def test_tailoring_accepted_when_platform_parent_exists(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        # Seed the platform parent first
        await client.post(
            "/api/v1/projects/platform/requirements/documents",
            json={"slug": "100-SPEC-ARCHITECTURE"},
            headers=_ACTOR_HEADERS,
        )
        await client.put(
            "/api/v1/projects/platform/requirements/documents/100-SPEC-ARCHITECTURE",
            json={"content": _PLATFORM_PARENT_DOC},
            headers={**_ACTOR_HEADERS, "If-Match": '"100-SPEC-ARCHITECTURE@v1"'},
        )
        # Now the tailored project document is valid
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-TAILORED"},
            headers=_ACTOR_HEADERS,
        )
        resp = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-TAILORED",
            json={"content": _TAILORED_DOC},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-TAILORED@v1"'},
        )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Event publication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_update_publishes_event(
    c5_app: FastAPI, c5_publisher: NullPublisher
) -> None:
    async with _client(c5_app) as client:
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers=_ACTOR_HEADERS,
        )
        await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-SAMPLE",
            json={"content": _DOC_BODY},
            headers={**_ACTOR_HEADERS, "If-Match": '"300-SPEC-SAMPLE@v1"'},
        )
    subjects = [subject for subject, _ in c5_publisher.published]
    assert any(s.endswith("document.created") for s in subjects)
    assert any(s.endswith("document.updated") for s in subjects)


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_x_user_id_returns_401(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.get("/api/v1/projects/demo/requirements/documents")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_write_without_role_returns_403(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-SAMPLE"},
            headers={"X-User-Id": "bob", "X-User-Roles": "user"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Stub endpoints
# ---------------------------------------------------------------------------


# The "import is deferred" assertion used to live here. It was removed
# when R-300-080 was implemented in v1.5; see
# `tests/integration/c5_requirements/test_import.py` for the real
# behaviour coverage.
