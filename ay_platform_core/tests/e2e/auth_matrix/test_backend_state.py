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


# ---------------------------------------------------------------------------
# C8 admin — LLM registry write observable in `llm_registry`
# ---------------------------------------------------------------------------


def _tenant_manager_headers(user_id: str = "u-bs-tmgr") -> dict[str, str]:
    return build_forward_auth_headers(
        RoleProfile(user_id=user_id, tenant_id=_TENANT, global_roles=("tenant_manager",))
    )


_PROVIDER_BODY = {
    "name": "BS-Anthropic",
    "base_url": "https://api.anthropic.com",
    "wire_format": "anthropic",
}


def _model_body(provider_id: str, alias: str) -> dict[str, object]:
    return {
        "alias": alias,
        "provider_id": provider_id,
        "upstream_model": "claude-haiku-4-5-20251001",
        "capabilities": {"vision": True, "tool_calling": True, "context_window": 200000},
        "provider_cost_in_per_1m": 0.80,
        "provider_cost_out_per_1m": 4.00,
        "default_model_quality": "low",
        "enabled": True,
    }


async def _create_provider(client: httpx.AsyncClient, name: str) -> str:
    resp = await client.post(
        "/admin/v1/llm/providers",
        headers=_tenant_manager_headers(),
        json={**_PROVIDER_BODY, "name": name},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["provider_id"])


async def test_c8_create_registry_model_persists_in_arango(
    auth_matrix_stack: PlatformStack,
) -> None:
    """POST /admin/v1/llm/registry SHALL insert a row into `llm_registry` keyed
    by the minted model_id, referencing the provider, with NO secret."""
    alias = f"bs-model-{uuid.uuid4().hex[:8]}"
    async with make_asgi_client(auth_matrix_stack.c8_admin_app) as client:
        pid = await _create_provider(client, f"prov-{uuid.uuid4().hex[:8]}")
        response = await client.post(
            "/admin/v1/llm/registry",
            headers=_tenant_manager_headers(),
            json=_model_body(pid, alias),
        )
    assert response.status_code == 201, response.text
    model_id = response.json()["model_id"]

    db = auth_matrix_stack.db_for("c8_admin")
    doc = await asyncio.to_thread(assert_arango_doc_exists, db, "llm_registry", model_id)
    assert doc.get("alias") == alias
    assert doc.get("provider_id") == pid
    assert "api_key_ciphertext" not in doc  # key lives on the provider


async def test_c8_provider_key_persists_ciphertext_not_plaintext(
    auth_matrix_stack: PlatformStack,
) -> None:
    """PUT /admin/v1/llm/providers/{id}/api-key SHALL store an encrypted token in
    `llm_providers`, NEVER the plaintext, and the response SHALL NOT echo it."""
    plaintext = f"sk-ant-{uuid.uuid4().hex}"
    async with make_asgi_client(auth_matrix_stack.c8_admin_app) as client:
        pid = await _create_provider(client, f"prov-key-{uuid.uuid4().hex[:8]}")
        keyed = await client.put(
            f"/admin/v1/llm/providers/{pid}/api-key",
            headers=_tenant_manager_headers(),
            json={"api_key": plaintext},
        )
    assert keyed.status_code == 200, keyed.text
    assert plaintext not in keyed.text
    assert keyed.json()["key_status"] == "set"

    db = auth_matrix_stack.db_for("c8_admin")
    doc = await asyncio.to_thread(assert_arango_doc_exists, db, "llm_providers", pid)
    ciphertext = doc.get("api_key_ciphertext")
    assert ciphertext is not None and ciphertext.startswith("ay.1.")
    assert plaintext not in str(doc)


def _admin_headers(user_id: str = "u-bs-admin-cat") -> dict[str, str]:
    return build_forward_auth_headers(
        RoleProfile(user_id=user_id, tenant_id=_TENANT, global_roles=("admin",))
    )


async def test_c8_catalog_upsert_persists_in_arango(
    auth_matrix_stack: PlatformStack,
) -> None:
    """PUT /api/v1/llm/catalog/{model_id} SHALL insert a row into
    `tenant_llm_catalog` keyed `{tenant}:{model_id}` — only after the model_id
    exists in the platform registry (the catalogue is a curated subset)."""
    alias = f"bs-cat-{uuid.uuid4().hex[:8]}"
    async with make_asgi_client(auth_matrix_stack.c8_admin_app) as client:
        pid = await _create_provider(client, f"prov-cat-{uuid.uuid4().hex[:8]}")
        reg = await client.post(
            "/admin/v1/llm/registry",
            headers=_tenant_manager_headers(),
            json=_model_body(pid, alias),
        )
        assert reg.status_code == 201, reg.text
        model_id = reg.json()["model_id"]
        cat = await client.put(
            f"/api/v1/llm/catalog/{model_id}",
            headers=_admin_headers(),
            json={"enabled": True, "markup_pct": 15.0},
        )
    assert cat.status_code == 200, cat.text
    assert cat.json()["registry"]["alias"] == alias

    db = auth_matrix_stack.db_for("c8_admin")
    doc = await asyncio.to_thread(
        assert_arango_doc_exists, db, "tenant_llm_catalog", f"{_TENANT}:{model_id}"
    )
    assert doc.get("tenant_id") == _TENANT
    assert doc.get("model_id") == model_id
    assert doc.get("markup_pct") == 15.0
    assert "api_key_ciphertext" not in doc


async def test_c8_catalog_upsert_unknown_model_is_404(
    auth_matrix_stack: PlatformStack,
) -> None:
    """A catalogue PUT for a model_id absent from the registry SHALL 404."""
    async with make_asgi_client(auth_matrix_stack.c8_admin_app) as client:
        resp = await client.put(
            f"/api/v1/llm/catalog/ghost-{uuid.uuid4().hex[:8]}",
            headers=_admin_headers("u-bs-admin-ghost"),
            json={"enabled": True},
        )
    assert resp.status_code == 404, resp.text


# Suppress unused-import warning for httpx (kept in case future tests
# use it directly rather than via make_asgi_client).
_: type = httpx.AsyncClient
