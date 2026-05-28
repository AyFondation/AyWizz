# =============================================================================
# File: test_documents_structural_ops.py
# Version: 1
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_documents_structural_ops.py
# Description: Integration tests for the live-docs operator-driven
#              structural ops (R-200-160..164) — `mkdir`, `rename`,
#              `move`. Reuses the `documents_app` fixture from
#              `test_documents_api.py` (real Arango + real MinIO
#              testcontainers + stubbed Gitea).
#
# @relation validates:R-200-160
# @relation validates:R-200-161
# @relation validates:R-200-162
# @relation validates:R-200-163
# =============================================================================

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

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
        base_url="http://e2e-doc-struct",
    )


async def _seed(c: httpx.AsyncClient, project: str, path: str, body: str) -> None:
    resp = await c.post(
        f"/api/v1/projects/{project}/documents",
        headers=_HEADERS,
        json={"path": path, "content": body},
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# mkdir (R-200-161)
# ---------------------------------------------------------------------------


async def test_mkdir_creates_keep_marker_and_pushes_to_gitea(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, fake_gitea = documents_app
    async with _client(app) as c:
        # Need at least one prior write to ensure the live-docs run
        # exists when mkdir is called on an empty corpus.
        await _seed(c, "proj-mkdir", "seed.md", "seed")
        resp = await c.post(
            "/api/v1/projects/proj-mkdir/documents/mkdir",
            headers=_HEADERS,
            json={"path": "notes"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["path"] == "notes"
        listing = await c.get(
            "/api/v1/projects/proj-mkdir/documents", headers=_HEADERS,
        )
        paths = {d["path"] for d in listing.json()["documents"]}
        assert "notes/.keep" in paths
        # Gitea saw a commit message labeled `mkdir`. `_FakeGiteaClient.commits`
        # is `dict[(owner, repo), list[GiteaCommit]]` — flatten before assert.
        all_commits = [
            c
            for (_owner, repo), bucket in fake_gitea.commits.items()
            if repo == "proj-mkdir"
            for c in bucket
        ]
        assert any("mkdir notes" in c.message for c in all_commits), fake_gitea.commits


async def test_mkdir_conflict_when_path_exists(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-mkdir-c", "notes/x.md", "x")
        resp = await c.post(
            "/api/v1/projects/proj-mkdir-c/documents/mkdir",
            headers=_HEADERS,
            json={"path": "notes"},
        )
        assert resp.status_code == 409


async def test_mkdir_rejects_bad_path(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-mkdir-bad", "seed.md", "x")
        for bad in ["/abs", "../escape", "back\\slash"]:
            resp = await c.post(
                "/api/v1/projects/proj-mkdir-bad/documents/mkdir",
                headers=_HEADERS,
                json={"path": bad},
            )
            assert resp.status_code == 400, f"expected 400 for {bad!r}: {resp.text}"


async def test_mkdir_rejected_for_tenant_manager(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        resp = await c.post(
            "/api/v1/projects/proj-tm/documents/mkdir",
            headers=_HEADERS_TENANT_MANAGER,
            json={"path": "notes"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# rename (R-200-162 / R-200-163)
# ---------------------------------------------------------------------------


async def test_rename_file_moves_blob_atomically(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-rename", "draft.md", "hello")
        resp = await c.post(
            "/api/v1/projects/proj-rename/documents/rename",
            headers=_HEADERS,
            json={"from_path": "draft.md", "to_path": "final.md"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["from_path"] == "draft.md"
        assert body["to_path"] == "final.md"
        assert body["moved"] == 1
        # Old path now 404 ; new path serves the same content.
        old = await c.get(
            "/api/v1/projects/proj-rename/documents/draft.md", headers=_HEADERS,
        )
        assert old.status_code == 404
        new = await c.get(
            "/api/v1/projects/proj-rename/documents/final.md", headers=_HEADERS,
        )
        assert new.status_code == 200
        assert new.content == b"hello"


async def test_rename_directory_recursive(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-rd", "old/a.md", "A")
        await _seed(c, "proj-rd", "old/b.md", "B")
        await _seed(c, "proj-rd", "old/sub/c.md", "C")
        resp = await c.post(
            "/api/v1/projects/proj-rd/documents/rename",
            headers=_HEADERS,
            json={"from_path": "old", "to_path": "new"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["moved"] == 3
        listing = await c.get(
            "/api/v1/projects/proj-rd/documents", headers=_HEADERS,
        )
        paths = {d["path"] for d in listing.json()["documents"]}
        assert paths == {"new/a.md", "new/b.md", "new/sub/c.md"}


async def test_rename_404_when_source_missing(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-rnf", "seed.md", "x")
        resp = await c.post(
            "/api/v1/projects/proj-rnf/documents/rename",
            headers=_HEADERS,
            json={"from_path": "ghost.md", "to_path": "found.md"},
        )
        assert resp.status_code == 404


async def test_rename_409_when_target_exists(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-rc", "a.md", "A")
        await _seed(c, "proj-rc", "b.md", "B")
        resp = await c.post(
            "/api/v1/projects/proj-rc/documents/rename",
            headers=_HEADERS,
            json={"from_path": "a.md", "to_path": "b.md"},
        )
        assert resp.status_code == 409


async def test_rename_400_on_same_path(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-rs", "a.md", "A")
        resp = await c.post(
            "/api/v1/projects/proj-rs/documents/rename",
            headers=_HEADERS,
            json={"from_path": "a.md", "to_path": "a.md"},
        )
        assert resp.status_code == 400


async def test_rename_400_on_cycle(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-rcy", "dir/a.md", "A")
        resp = await c.post(
            "/api/v1/projects/proj-rcy/documents/rename",
            headers=_HEADERS,
            json={"from_path": "dir", "to_path": "dir/sub"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# move (R-200-162 — reduces to rename with composed target)
# ---------------------------------------------------------------------------


async def test_move_file_under_directory(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-mv", "report.md", "R")
        resp = await c.post(
            "/api/v1/projects/proj-mv/documents/move",
            headers=_HEADERS,
            json={"from_path": "report.md", "to_dir": "archive/2026"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["from_path"] == "report.md"
        assert body["to_dir"] == "archive/2026"
        listing = await c.get(
            "/api/v1/projects/proj-mv/documents", headers=_HEADERS,
        )
        paths = {d["path"] for d in listing.json()["documents"]}
        assert "archive/2026/report.md" in paths
        assert "report.md" not in paths


async def test_move_400_when_target_equals_source_parent(
    documents_app: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """Moving `archive/report.md` to `to_dir=archive` is a no-op move
    where the composed target equals the source — must 400."""
    app, _ = documents_app
    async with _client(app) as c:
        await _seed(c, "proj-mv2", "archive/report.md", "R")
        resp = await c.post(
            "/api/v1/projects/proj-mv2/documents/move",
            headers=_HEADERS,
            json={"from_path": "archive/report.md", "to_dir": "archive"},
        )
        assert resp.status_code == 400
