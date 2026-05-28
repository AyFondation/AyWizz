# =============================================================================
# File: test_documents_api.py
# Version: 3
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_documents_api.py
# Description: Integration tests for the chat-direct DocGen document
#              CRUD surface (D-015 / R-200-153..156). Exercises
#              POST / PUT / GET (list + read) / DELETE end-to-end
#              against real ArangoDB + real MinIO testcontainers and a
#              stubbed Gitea client, through the FastAPI router.
#
#              v3 (2026-05-21): live-docs per-file version (R-200-147).
#              Asserts that the `X-Turn-Id` header batches the tree's
#              `ArtifactNode.version` by AI response — multiple writes in
#              one response collapse to one bump, distinct responses add
#              up, per-file histories stay isolated, and an untagged
#              (operator) write reads as v1.
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from tests.integration.c2_auth.test_gitea_provisioning import _FakeGiteaClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-doc",
    "X-User-Roles": "project_editor,admin",
}
_HEADERS_TENANT_MANAGER = {
    "X-User-Id": "tm",
    "X-Tenant-Id": "tenant-doc",
    "X-User-Roles": "tenant_manager",
}


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-doc",
    )


# ---------------------------------------------------------------------------
# CRUD happy path + Gitea push
# ---------------------------------------------------------------------------


async def test_full_document_lifecycle(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """R-200-153..155 : create → list → read → update → delete, with
    incremental Gitea push observed on each write."""
    app, fake_gitea = documents_app
    async with _client(app) as c:
        # Empty list before any write.
        listing = await c.get(
            "/api/v1/projects/proj-d/documents", headers=_HEADERS,
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()["documents"] == []

        # Create.
        created = await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_HEADERS,
            json={"path": "docs/intro.md", "content": "# Intro\n\nHello.\n"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["path"] == "docs/intro.md"
        assert body["size_bytes"] > 0

        # Gitea push fired (one commit). Owner is svc-{tenant}-{project}.
        key = ("svc-tenant-doc-proj-d", "proj-d")
        assert key in fake_gitea.files
        assert fake_gitea.files[key]["docs/intro.md"] == b"# Intro\n\nHello.\n"
        assert len(fake_gitea.commits.get(key, [])) == 1

        # List shows the doc.
        listing = await c.get(
            "/api/v1/projects/proj-d/documents", headers=_HEADERS,
        )
        assert listing.status_code == 200
        paths = {d["path"] for d in listing.json()["documents"]}
        assert paths == {"docs/intro.md"}

        # Read returns the content.
        read = await c.get(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_HEADERS,
        )
        assert read.status_code == 200, read.text
        assert read.text == "# Intro\n\nHello.\n"

        # Update via PUT — overwrites + new Gitea commit.
        updated = await c.put(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_HEADERS,
            json={"content": "# Intro v2\n\nUpdated.\n"},
        )
        assert updated.status_code == 200, updated.text
        read2 = await c.get(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_HEADERS,
        )
        assert read2.text == "# Intro v2\n\nUpdated.\n"
        assert len(fake_gitea.commits.get(key, [])) == 2

        # Delete — 204, then 404 on subsequent read.
        deleted = await c.delete(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_HEADERS,
        )
        assert deleted.status_code == 204, deleted.text
        gone = await c.get(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_HEADERS,
        )
        assert gone.status_code == 404
        listing = await c.get(
            "/api/v1/projects/proj-d/documents", headers=_HEADERS,
        )
        assert listing.json()["documents"] == []


async def test_path_traversal_rejected(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """R-200-153 : `..` / leading `/` / backslash SHALL be 400."""
    app, _ = documents_app
    async with _client(app) as c:
        for bad in ("../escape.md", "a\\b.md"):
            r = await c.post(
                "/api/v1/projects/proj-d/documents",
                headers=_HEADERS,
                json={"path": bad, "content": "x"},
            )
            assert r.status_code == 400, f"{bad!r} should be 400 (got {r.status_code})"


async def test_tenant_manager_rejected(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """E-100-002 v2 : tenant_manager is content-blind → 403 on every
    document verb."""
    app, _ = documents_app
    async with _client(app) as c:
        assert (
            await c.get(
                "/api/v1/projects/proj-d/documents",
                headers=_HEADERS_TENANT_MANAGER,
            )
        ).status_code == 403
        assert (
            await c.post(
                "/api/v1/projects/proj-d/documents",
                headers=_HEADERS_TENANT_MANAGER,
                json={"path": "x.md", "content": "y"},
            )
        ).status_code == 403


async def test_read_missing_document_404(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """A path that was never written → 404 (run exists check + blob
    miss). Also covers the no-run-yet case (list-empty → read 404)."""
    app, _ = documents_app
    async with _client(app) as c:
        # No run yet at all.
        r = await c.get(
            "/api/v1/projects/proj-d/documents/never.md", headers=_HEADERS,
        )
        assert r.status_code == 404
        # Create one doc, then read a different missing path.
        await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_HEADERS,
            json={"path": "a.md", "content": "a"},
        )
        r2 = await c.get(
            "/api/v1/projects/proj-d/documents/b.md", headers=_HEADERS,
        )
        assert r2.status_code == 404


async def test_gitea_failure_does_not_break_write(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """R-200-155 best-effort : a Gitea outage SHALL NOT fail the write
    — MinIO stays the source of truth."""
    app, fake_gitea = documents_app
    fake_gitea.fail_on_create_file = True
    async with _client(app) as c:
        created = await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_HEADERS,
            json={"path": "resilient.md", "content": "still saved"},
        )
        assert created.status_code == 201, created.text
        # MinIO read still works despite the Gitea failure.
        read = await c.get(
            "/api/v1/projects/proj-d/documents/resilient.md",
            headers=_HEADERS,
        )
        assert read.status_code == 200
        assert read.text == "still saved"
        # No commit landed on the stub (it raised on every call).
        key = ("svc-tenant-doc-proj-d", "proj-d")
        assert fake_gitea.commits.get(key, []) == []


# ---------------------------------------------------------------------------
# Per-file version, batched by AI response (R-200-147)
# ---------------------------------------------------------------------------


def _headers_turn(turn_id: str) -> dict[str, str]:
    return {**_HEADERS, "X-Turn-Id": turn_id}


async def test_live_docs_version_batches_per_response(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """The tree's `ArtifactNode.version` counts DISTINCT AI responses
    that touched each file. Two writes in one response collapse to one
    bump ; a second response adds one ; per-file histories stay
    isolated ; an untagged (operator) write reads as v1."""
    app, _ = documents_app
    service: ArtifactsService = app.state.artifacts_service
    async with _client(app) as c:
        # Response r1 writes intro.md twice (create then a correction)
        # and also creates other.md — all under the same turn id.
        for content in ("# Intro\n", "# Intro fixed\n"):
            r = await c.post(
                "/api/v1/projects/proj-d/documents",
                headers=_headers_turn("r1"),
                json={"path": "docs/intro.md", "content": content},
            )
            assert r.status_code == 201, r.text
        r = await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_headers_turn("r1"),
            json={"path": "docs/other.md", "content": "# Other\n"},
        )
        assert r.status_code == 201, r.text
        # Response r2 updates intro.md only.
        r = await c.put(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_headers_turn("r2"),
            json={"content": "# Intro v3\n"},
        )
        assert r.status_code == 200, r.text
        # An operator write with no turn id (outside the chat loop).
        r = await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_HEADERS,
            json={"path": "docs/manual.md", "content": "# Manual\n"},
        )
        assert r.status_code == 201, r.text

        tree = await service.get_tree(
            run_id=service.LIVE_DOCS_RUN_ID,
            project_id="proj-d",
            tenant_id="tenant-doc",
        )
        versions = {n.path: n.version for n in tree.nodes}
        # intro.md touched by r1 (x2) and r2 → 2 distinct responses.
        assert versions["docs/intro.md"] == 2
        # other.md touched only by r1 → 1 (isolation : r2 not counted).
        assert versions["docs/other.md"] == 1
        # manual.md untagged → falls back to v1.
        assert versions["docs/manual.md"] == 1


async def test_non_live_docs_run_has_no_version(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """Version is a live-docs concern : a non-live-docs run's tree nodes
    leave `version` as None (the UX renders no badge)."""
    app, _ = documents_app
    service: ArtifactsService = app.state.artifacts_service
    run_id = await service.create_run(project_id="proj-d", tenant_id="tenant-doc")
    await service.put_file(
        run_id=run_id,
        project_id="proj-d",
        tenant_id="tenant-doc",
        relative_path="src/main.py",
        data=b"print('x')\n",
    )
    tree = await service.get_tree(
        run_id=run_id, project_id="proj-d", tenant_id="tenant-doc",
    )
    assert tree.nodes
    assert all(n.version is None for n in tree.nodes)


# ---------------------------------------------------------------------------
# Version history viewer (R-200-147) — per-file history + read-at-ref
# ---------------------------------------------------------------------------


async def test_read_document_at_ref_returns_historical_content(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """`GET /documents/{path}?ref=<sha>` returns the document as it was
    at that commit (R-200-147), while the no-ref read returns latest.
    The per-file history (list_commits(path=...)) lists both revisions
    most-recent-first."""
    app, _ = documents_app
    service: ArtifactsService = app.state.artifacts_service
    async with _client(app) as c:
        r = await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_headers_turn("r1"),
            json={"path": "docs/intro.md", "content": "# v1\n"},
        )
        assert r.status_code == 201, r.text
        r = await c.put(
            "/api/v1/projects/proj-d/documents/docs/intro.md",
            headers=_headers_turn("r2"),
            json={"content": "# v2\n"},
        )
        assert r.status_code == 200, r.text

        # Latest read (no ref) → v2.
        latest = await c.get(
            "/api/v1/projects/proj-d/documents/docs/intro.md", headers=_HEADERS,
        )
        assert latest.text == "# v2\n"

        # Per-file history → 2 commits, most recent first.
        hist = await service.list_commits(
            project_id="proj-d", tenant_id="tenant-doc", path="docs/intro.md",
        )
        assert len(hist) == 2
        oldest_sha = hist[-1]["sha"]

        # Read at the first revision → v1.
        at_ref = await c.get(
            f"/api/v1/projects/proj-d/documents/docs/intro.md?ref={oldest_sha}",
            headers=_HEADERS,
        )
        assert at_ref.status_code == 200, at_ref.text
        assert at_ref.text == "# v1\n"


async def test_read_document_at_unknown_ref_is_404(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """A ref that never touched the file (or doesn't exist) → 404."""
    app, _ = documents_app
    async with _client(app) as c:
        await c.post(
            "/api/v1/projects/proj-d/documents",
            headers=_HEADERS,
            json={"path": "a.md", "content": "x"},
        )
        r = await c.get(
            "/api/v1/projects/proj-d/documents/a.md?ref=does-not-exist",
            headers=_HEADERS,
        )
        assert r.status_code == 404, r.text
