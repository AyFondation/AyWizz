# =============================================================================
# File: test_backend_state.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/test_backend_state.py
# Description: Backend-state assertions on write/delete endpoints.
#              For each catalogued endpoint with `backend != NONE`,
#              the test issues an authenticated request with a real
#              valid body, then queries the persistence layer
#              DIRECTLY (ArangoDB / MinIO) to confirm the side effect.
#              Backend assertions live in `_backend.py`.
#
#              These are HAND-WRITTEN per resource type (a generic
#              parametrised approach can't know each resource's body
#              schema or `_key` composition). The catalog tells us
#              WHICH endpoints have backend persistence; this file
#              implements the assertion for each.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest

from tests.e2e.auth_matrix._backend import (
    assert_arango_doc_absent,
    assert_arango_doc_exists,
)
from tests.e2e.auth_matrix._clients import (
    RoleProfile,
    build_bearer_headers,
    build_forward_auth_headers,
    make_asgi_client,
)
from tests.e2e.auth_matrix._stack import PlatformStack

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


_TENANT = "tenant-bs"
_PROJECT = "project-bs"


def _content_user_headers(user_id: str, project_role: str = "project_owner") -> dict[str, str]:
    return build_forward_auth_headers(
        RoleProfile(
            user_id=user_id,
            tenant_id=_TENANT,
            project_id=_PROJECT,
            project_role=project_role,
        )
    )


# ---------------------------------------------------------------------------
# C5 — documents create + delete observable in `req_documents`
# ---------------------------------------------------------------------------


async def test_c5_create_document_persists_in_arango(
    auth_matrix_stack: PlatformStack,
) -> None:
    """POST /requirements/documents SHALL insert a row into `req_documents`
    with key `{project_id}:{slug}`. The HTTP 201 alone is not enough —
    we query Arango directly to prove persistence."""
    # Slug must match `NNN-<KIND>-<SLUG>` per C5's DocumentFrontmatter validator.
    slug = f"700-TEST-BS-DOC-{uuid.uuid4().hex[:6].upper()}"
    headers = _content_user_headers("u-bs-create", project_role="project_editor")
    body = {"slug": slug, "language": "en", "status": "draft", "derives_from": []}

    async with make_asgi_client(auth_matrix_stack.c5_app) as client:
        response = await client.post(
            f"/api/v1/projects/{_PROJECT}/requirements/documents",
            headers=headers,
            json=body,
        )
    assert response.status_code == 201, response.text

    db = auth_matrix_stack.db_for("c5_requirements")
    expected_key = f"{_PROJECT}:{slug}"
    doc = await asyncio.to_thread(
        assert_arango_doc_exists, db, "req_documents", expected_key
    )
    assert doc.get("project_id") == _PROJECT
    assert doc.get("slug") == slug


async def test_c5_delete_document_removes_arango_row(
    auth_matrix_stack: PlatformStack,
) -> None:
    """Create then DELETE; the row SHALL be gone from `req_documents`."""
    slug = f"700-TEST-BS-DEL-{uuid.uuid4().hex[:6].upper()}"
    create_headers = _content_user_headers("u-bs-del-create", "project_editor")
    delete_headers = _content_user_headers("u-bs-del-delete", "project_owner")

    async with make_asgi_client(auth_matrix_stack.c5_app) as client:
        create = await client.post(
            f"/api/v1/projects/{_PROJECT}/requirements/documents",
            headers=create_headers,
            json={"slug": slug, "language": "en", "status": "draft",
                  "derives_from": []},
        )
        assert create.status_code == 201, create.text
        delete = await client.delete(
            f"/api/v1/projects/{_PROJECT}/requirements/documents/{slug}",
            headers=delete_headers,
        )
    assert delete.status_code == 204, delete.text

    db = auth_matrix_stack.db_for("c5_requirements")
    await asyncio.to_thread(
        assert_arango_doc_absent, db, "req_documents", f"{_PROJECT}:{slug}"
    )


# ---------------------------------------------------------------------------
# C7 — source ingest observable in `memory_sources`
# ---------------------------------------------------------------------------


async def test_c7_ingest_source_persists_in_arango(
    auth_matrix_stack: PlatformStack,
) -> None:
    """POST /memory/projects/{p}/sources SHALL insert a row into
    `memory_sources` for the given source_id."""
    source_id = f"bs-src-{uuid.uuid4().hex[:8]}"
    headers = _content_user_headers("u-bs-c7", "project_editor")
    body = {
        "source_id": source_id,
        "project_id": _PROJECT,
        "mime_type": "text/plain",
        "content": "This is test content for backend state assertion.",
        "size_bytes": 50,
        "uploaded_by": "u-bs-c7",
    }
    async with make_asgi_client(auth_matrix_stack.c7_app) as client:
        response = await client.post(
            f"/api/v1/memory/projects/{_PROJECT}/sources",
            headers=headers,
            json=body,
        )
    assert response.status_code == 201, response.text

    # The c7 source `_key` is composed by the repository; rather than
    # depending on the exact format we count rows for this project's
    # source_id.
    db = auth_matrix_stack.db_for("c7_memory")

    def _query() -> list[dict[str, Any]]:
        cursor = db.aql.execute(
            "FOR s IN memory_sources FILTER s.source_id == @sid RETURN s",
            bind_vars={"sid": source_id},
        )
        return list(cursor)

    rows = await asyncio.to_thread(_query)
    assert len(rows) == 1, (
        f"expected exactly 1 row for source_id={source_id} in memory_sources, "
        f"got {len(rows)}: {rows}"
    )
    assert rows[0]["project_id"] == _PROJECT


# ---------------------------------------------------------------------------
# C2 — user create observable in `c2_users`
# ---------------------------------------------------------------------------


async def test_c2_create_user_persists_in_arango(
    auth_matrix_stack: PlatformStack,
) -> None:
    """POST /auth/users SHALL insert a row into `c2_users`. Admin
    bearer JWT required (the C2 admin endpoints validate JWT, not
    forward-auth headers)."""
    admin_profile = RoleProfile(
        user_id="bs-admin",
        tenant_id=_TENANT,
        global_roles=("admin",),
    )
    headers = await build_bearer_headers(auth_matrix_stack.c2_service, admin_profile)
    username = f"bs-user-{uuid.uuid4().hex[:8]}@auth-matrix.test"
    body = {
        "username": username,
        "password": "BackendStateTest1!",
        "tenant_id": _TENANT,
        "roles": ["user"],
        "email": username,
    }
    async with make_asgi_client(auth_matrix_stack.c2_app) as client:
        response = await client.post("/auth/users", headers=headers, json=body)
    assert response.status_code == 201, response.text
    user_id = response.json().get("user_id")
    assert user_id

    db = auth_matrix_stack.db_for("c2_auth")

    def _query() -> list[dict[str, Any]]:
        cursor = db.aql.execute(
            "FOR u IN c2_users FILTER u.username == @uname RETURN u",
            bind_vars={"uname": username},
        )
        return list(cursor)

    rows = await asyncio.to_thread(_query)
    assert len(rows) == 1, f"user `{username}` not in c2_users (got {rows})"
    persisted = rows[0]
    assert persisted["tenant_id"] == _TENANT
    # Password hash SHALL NOT be the plaintext password (R-100-035).
    assert persisted.get("password_hash") != body["password"]


# Suppress unused-import warning for httpx (kept in case future tests
# use it directly rather than via make_asgi_client).
_: type = httpx.AsyncClient
