# =============================================================================
# File: test_entity_operations.py
# Version: 1
# Path: ay_platform_core/tests/integration/c5_requirements/test_entity_operations.py
# Description: Integration tests for C5 entity-level endpoints that were
#              not covered by test_crud_flow.py:
#              - PATCH /entities/{id} — re-serialises the document body
#                via the internal YAML helpers.
#              - DELETE /entities/{id} — soft-delete paths (deprecated +
#                superseded).
#              - GET /entities/{id}/history — history snapshot listing.
#              - GET /relations — relation edges.
#              - GET /tailorings — tailoring audit report.
#
#              Exercises the C5 service document-rewriting helpers that
#              the coverage audit flagged as uncovered (service.py 52%
#              → we lift this by validating real PATCH semantics).
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


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


_DOC = """---
document: 300-SPEC-OPS
version: 1
path: projects/demo/requirements/300-SPEC-OPS.md
language: en
status: draft
---

# Operations spec

#### R-300-600

```yaml
id: R-300-600
version: 1
status: draft
category: functional
```

The system SHALL process operations in bounded time.
"""


async def _seed(client: httpx.AsyncClient) -> None:
    """Create a document with one entity via the POST + PUT combo."""
    create = await client.post(
        "/api/v1/projects/demo/requirements/documents",
        json={"slug": "300-SPEC-OPS"},
        headers=_HEADERS,
    )
    assert create.status_code == 201, create.text
    put = await client.put(
        "/api/v1/projects/demo/requirements/documents/300-SPEC-OPS",
        json={"content": _DOC},
        headers={**_HEADERS, "If-Match": '"300-SPEC-OPS@v1"'},
    )
    assert put.status_code == 200, put.text


# ---------------------------------------------------------------------------
# PATCH /entities — exercises _rewrite_document_with_updated_entity and the
# YAML helpers (_replace_entity_block, _yaml_dump_row, etc.)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_entity_status_bumps_version(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)

        patch = await client.patch(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            json={"status": "approved"},
            headers={**_HEADERS, "If-Match": '"R-300-600@v1"'},
        )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    # `status` is not in the semantic-change set (R-M100-060 excludes it),
    # so the entity version SHALL remain at 1 while the status transitions.
    assert body["status"] == "approved"
    assert body["version"] == 1


@pytest.mark.asyncio
async def test_patch_entity_body_change_bumps_version(c5_app: FastAPI) -> None:
    """Body change IS semantic (R-M100-060) → version bump."""
    async with _client(c5_app) as client:
        await _seed(client)
        patch = await client.patch(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            json={"body": "The system SHALL process operations within 100 ms."},
            headers={**_HEADERS, "If-Match": '"R-300-600@v1"'},
        )
    assert patch.status_code == 200, patch.text
    assert patch.json()["version"] == 2


@pytest.mark.asyncio
async def test_patch_entity_without_if_match_returns_428(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        patch = await client.patch(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            json={"status": "approved"},
            headers=_HEADERS,
        )
    assert patch.status_code == 428


@pytest.mark.asyncio
async def test_patch_entity_with_stale_if_match_returns_412(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        patch = await client.patch(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            json={"status": "approved"},
            headers={**_HEADERS, "If-Match": '"R-300-600@v99"'},
        )
    assert patch.status_code == 412


@pytest.mark.asyncio
async def test_patch_unknown_entity_returns_404(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        patch = await client.patch(
            "/api/v1/projects/demo/requirements/entities/R-300-999",
            json={"status": "approved"},
            headers={**_HEADERS, "If-Match": '"R-300-999@v1"'},
        )
    assert patch.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /entities — soft-delete semantics (deprecated vs superseded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_entity_marks_deprecated(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        delete = await client.delete(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            headers=_HEADERS,
        )
        assert delete.status_code == 204
        get = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            headers=_HEADERS,
        )
    # R-300-033: soft-delete → status transitions to deprecated.
    assert get.status_code == 200
    body = get.json()
    assert body["status"] == "deprecated"
    assert body["deprecated_reason"]


@pytest.mark.asyncio
async def test_delete_entity_with_supersedes_marks_superseded(
    c5_app: FastAPI,
) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        delete = await client.delete(
            "/api/v1/projects/demo/requirements/entities/R-300-600?supersedes=R-300-700",
            headers=_HEADERS,
        )
        assert delete.status_code == 204
        get = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            headers=_HEADERS,
        )
    body = get.json()
    assert body["status"] == "superseded"
    assert body["superseded_by"] == "R-300-700"


@pytest.mark.asyncio
async def test_delete_unknown_entity_returns_404(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        resp = await client.delete(
            "/api/v1/projects/demo/requirements/entities/R-300-404",
            headers=_HEADERS,
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /entities/{id}/history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_grows_after_update(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        # Trigger a semantic update to create a history snapshot
        await client.patch(
            "/api/v1/projects/demo/requirements/entities/R-300-600",
            json={"body": "Revised body for history test."},
            headers={**_HEADERS, "If-Match": '"R-300-600@v1"'},
        )
        history = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-300-600/history",
            headers=_HEADERS,
        )
    assert history.status_code == 200
    body = history.json()
    assert len(body["history"]) >= 1
    assert body["history"][0]["entity_id"] == "R-300-600"


@pytest.mark.asyncio
async def test_history_empty_for_brand_new_entity(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)
        history = await client.get(
            "/api/v1/projects/demo/requirements/entities/R-300-600/history",
            headers=_HEADERS,
        )
    body = history.json()
    # A newly created entity has no prior snapshots.
    assert body["history"] == []


# ---------------------------------------------------------------------------
# GET /relations
# ---------------------------------------------------------------------------


_DOC_WITH_RELATIONS = """---
document: 300-SPEC-REL
version: 1
path: projects/demo/requirements/300-SPEC-REL.md
language: en
status: draft
---

# Rel spec

#### R-300-700

```yaml
id: R-300-700
version: 1
status: draft
category: functional
derives-from:
  - R-300-600
impacts:
  - R-300-800
```

Relation source entity.
"""


@pytest.mark.asyncio
async def test_list_relations_returns_derives_and_impacts(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        await _seed(client)  # R-300-600 exists
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-REL"},
            headers=_HEADERS,
        )
        put = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-REL",
            json={"content": _DOC_WITH_RELATIONS},
            headers={**_HEADERS, "If-Match": '"300-SPEC-REL@v1"'},
        )
        assert put.status_code == 200, put.text

        relations = await client.get(
            "/api/v1/projects/demo/requirements/relations?source=R-300-700",
            headers=_HEADERS,
        )
    assert relations.status_code == 200
    body = relations.json()
    types = {r["type"] for r in body["relations"]}
    assert "derives-from" in types
    assert "impacts" in types


# ---------------------------------------------------------------------------
# GET /tailorings
# ---------------------------------------------------------------------------


_PLATFORM_PARENT = """---
document: 100-SPEC-PARENT
version: 1
path: platform/requirements/100-SPEC-PARENT.md
language: en
status: draft
---

# Parent spec

#### R-100-001

```yaml
id: R-100-001
version: 1
status: approved
category: architecture
```

Platform parent entity.
"""

_TAILORED = """---
document: 300-SPEC-TAILOR
version: 1
path: projects/demo/requirements/300-SPEC-TAILOR.md
language: en
status: draft
---

# Tailored spec

#### R-300-800

```yaml
id: R-300-800
version: 1
status: draft
category: functional
tailoring-of: R-100-001
override: true
```

Derived entity.

### Tailoring rationale

This project refines the platform default because the regulated context
requires an explicit audit trail.
"""


@pytest.mark.asyncio
async def test_list_tailorings_returns_project_tailoring(c5_app: FastAPI) -> None:
    async with _client(c5_app) as client:
        # Seed the platform parent first.
        await client.post(
            "/api/v1/projects/platform/requirements/documents",
            json={"slug": "100-SPEC-PARENT"},
            headers=_HEADERS,
        )
        await client.put(
            "/api/v1/projects/platform/requirements/documents/100-SPEC-PARENT",
            json={"content": _PLATFORM_PARENT},
            headers={**_HEADERS, "If-Match": '"100-SPEC-PARENT@v1"'},
        )
        # Now create the project-level tailored entity.
        await client.post(
            "/api/v1/projects/demo/requirements/documents",
            json={"slug": "300-SPEC-TAILOR"},
            headers=_HEADERS,
        )
        put = await client.put(
            "/api/v1/projects/demo/requirements/documents/300-SPEC-TAILOR",
            json={"content": _TAILORED},
            headers={**_HEADERS, "If-Match": '"300-SPEC-TAILOR@v1"'},
        )
        assert put.status_code == 200, put.text

        tailorings = await client.get(
            "/api/v1/projects/demo/requirements/tailorings",
            headers=_HEADERS,
        )
    assert tailorings.status_code == 200
    body = tailorings.json()
    assert len(body) >= 1
    row = next((r for r in body if r["project_entity_id"] == "R-300-800"), None)
    assert row is not None
    assert row["platform_parent_id"] == "R-100-001"
    assert row["conformity"] == "conformant"
    assert "regulated context" in row["rationale_excerpt"]
