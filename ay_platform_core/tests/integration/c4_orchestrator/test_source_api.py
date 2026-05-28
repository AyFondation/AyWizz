# =============================================================================
# File: test_source_api.py
# Version: 1
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_source_api.py
# Description: Integration tests for the project source-files surface
#              (§5.18 — R-200-170..174) : tree projection, mkdir /
#              rename / move (editor+ RBAC), file metadata. Real Arango
#              + real MinIO via testcontainers, fake Gitea stub.
#
# @relation validates:R-200-170
# @relation validates:R-200-171
# @relation validates:R-200-172
# @relation validates:R-200-173
# @relation validates:R-200-174
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c4_orchestrator.artifacts_models import ArtifactRunStatus
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from ay_platform_core.c4_orchestrator.artifacts_storage import ArtifactStorage
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from ay_platform_core.c4_orchestrator.source_router import (
    router as source_router,
)
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
)
from tests.integration.c2_auth.test_gitea_provisioning import _FakeGiteaClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


_HEADERS_EDITOR = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-src",
    "X-User-Roles": "project_editor",
}
_HEADERS_VIEWER = {
    "X-User-Id": "bob",
    "X-Tenant-Id": "tenant-src",
    "X-User-Roles": "project_viewer",
}
_HEADERS_TENANT_MANAGER = {
    "X-User-Id": "tm",
    "X-Tenant-Id": "tenant-src",
    "X-User-Roles": "tenant_manager",
}


@pytest_asyncio.fixture(scope="function")
async def source_app(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str]]:
    """Source app with a pre-seeded artifact run holding `src/main.py`
    + `tests/test_main.py` so tree/rename/move tests have material."""
    db_name = f"c4_src_{uuid.uuid4().hex[:8]}"
    bucket = f"srcbucket-{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    db = ArangoClient(hosts=arango_container.url).db(
        db_name, username="root", password=arango_container.password,
    )
    repo = OrchestratorRepository(db)
    repo._ensure_collections_sync()

    minio_client = Minio(
        minio_container.endpoint,
        access_key=minio_container.access_key,
        secret_key=minio_container.secret_key,
        secure=False,
    )
    storage = ArtifactStorage(minio_client, bucket)
    await storage.ensure_bucket()
    fake_gitea = _FakeGiteaClient()
    service = ArtifactsService(
        repo=repo, storage=storage, gitea=fake_gitea,  # type: ignore[arg-type]
    )

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    await service.create_run(
        project_id="proj-src",
        tenant_id="tenant-src",
        run_id=run_id,
        label="seed-source-run",
    )
    await service.put_file(
        run_id=run_id,
        project_id="proj-src",
        tenant_id="tenant-src",
        relative_path="src/main.py",
        data=b"print('hello')\n",
    )
    await service.put_file(
        run_id=run_id,
        project_id="proj-src",
        tenant_id="tenant-src",
        relative_path="tests/test_main.py",
        data=b"def test_smoke(): assert True\n",
    )
    await service.mark_completed(run_id=run_id, status_=ArtifactRunStatus.COMPLETED)

    app = FastAPI()
    app.include_router(source_router)
    app.state.artifacts_service = service
    try:
        yield app, service, fake_gitea, run_id
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-src",
    )


# ---------------------------------------------------------------------------
# GET /source/tree (R-200-170)
# ---------------------------------------------------------------------------


async def test_tree_returns_recursive_structure(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.get(
            f"/api/v1/projects/proj-src/source/tree?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    # Top-level : two dirs (src, tests), no top-level files in the seed.
    top_names = {n["name"] for n in body["nodes"]}
    assert top_names == {"src", "tests"}
    src_node = next(n for n in body["nodes"] if n["name"] == "src")
    assert src_node["kind"] == "dir"
    children = src_node["children"]
    assert len(children) == 1
    assert children[0]["name"] == "main.py"
    assert children[0]["kind"] == "file"
    assert children[0]["size_bytes"] > 0


async def test_tree_rejected_for_tenant_manager(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.get(
            f"/api/v1/projects/proj-src/source/tree?run_id={run_id}",
            headers=_HEADERS_TENANT_MANAGER,
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# mkdir / rename / move RBAC (R-200-171)
# ---------------------------------------------------------------------------


async def test_mkdir_creates_keep_marker(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, fake_gitea, run_id = source_app
    async with _client(app) as c:
        resp = await c.post(
            f"/api/v1/projects/proj-src/source/mkdir?run_id={run_id}",
            headers=_HEADERS_EDITOR,
            json={"path": "docs"},
        )
        assert resp.status_code == 201, resp.text
        # And the tree now shows the new dir with its `.keep` marker.
        tree = await c.get(
            f"/api/v1/projects/proj-src/source/tree?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    names = {n["name"] for n in tree.json()["nodes"]}
    assert "docs" in names
    all_commits = [
        c
        for (_owner, repo), bucket in fake_gitea.commits.items()
        if repo == "proj-src"
        for c in bucket
    ]
    assert any("source — mkdir docs" in c.message for c in all_commits)


async def test_mkdir_403_for_viewer(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.post(
            f"/api/v1/projects/proj-src/source/mkdir?run_id={run_id}",
            headers=_HEADERS_VIEWER,
            json={"path": "docs"},
        )
    assert resp.status_code == 403


async def test_rename_file(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.post(
            f"/api/v1/projects/proj-src/source/rename?run_id={run_id}",
            headers=_HEADERS_EDITOR,
            json={"from_path": "src/main.py", "to_path": "src/entrypoint.py"},
        )
        assert resp.status_code == 200, resp.text
        tree = await c.get(
            f"/api/v1/projects/proj-src/source/tree?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    src_node = next(n for n in tree.json()["nodes"] if n["name"] == "src")
    child_names = {c["name"] for c in src_node["children"]}
    assert child_names == {"entrypoint.py"}


async def test_move_file_to_new_dir(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.post(
            f"/api/v1/projects/proj-src/source/move?run_id={run_id}",
            headers=_HEADERS_EDITOR,
            json={"from_path": "tests/test_main.py", "to_dir": "src/tests"},
        )
        assert resp.status_code == 200, resp.text
        tree = await c.get(
            f"/api/v1/projects/proj-src/source/tree?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    # After moving, `tests/` directory has no more files — it disappears
    # from the recursive projection (no `.keep` to anchor it).
    top_names = {n["name"] for n in tree.json()["nodes"]}
    assert "tests" not in top_names
    src_node: dict[str, Any] = next(
        n for n in tree.json()["nodes"] if n["name"] == "src"
    )
    src_children_names = {c["name"] for c in src_node["children"]}
    assert "tests" in src_children_names


# ---------------------------------------------------------------------------
# GET /source/file/{path}/meta (R-200-173)
# ---------------------------------------------------------------------------


async def test_file_meta_returns_size_and_mime(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, svc, _g, run_id = source_app
    # Seed a top-level (single-segment) file so the URL fits the
    # `/source/file/{name}/meta` shape the coherence pattern-matcher
    # expects ; multi-segment {path:path} also works at runtime but
    # the test-coverage check counts segments.
    await svc.put_file(
        run_id=run_id,
        project_id="proj-src",
        tenant_id="tenant-src",
        relative_path="README.md",
        data=b"# Project source\n",
    )
    async with _client(app) as c:
        resp = await c.get(
            f"/api/v1/projects/proj-src/source/file/README.md/meta?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == "README.md"
    assert body["size"] > 0
    assert body["mime_type"].startswith("text/")


async def test_file_meta_404_for_missing_path(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.get(
            f"/api/v1/projects/proj-src/source/file/nofile.py/meta?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /source/file/{path}  (R-200-175 / P2.2.a)
# ---------------------------------------------------------------------------


async def test_delete_source_file_removes_blob_and_pushes_gitea(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, svc, fake_gitea, run_id = source_app
    # Seed a single-segment file for the {path:path} match per the
    # functional-coverage discipline of test_source_api.
    await svc.put_file(
        run_id=run_id,
        project_id="proj-src",
        tenant_id="tenant-src",
        relative_path="todelete.txt",
        data=b"bye\n",
    )
    async with _client(app) as c:
        resp = await c.delete(
            f"/api/v1/projects/proj-src/source/file/todelete.txt?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
        assert resp.status_code == 204, resp.text
        # The file is gone from the tree.
        tree = await c.get(
            f"/api/v1/projects/proj-src/source/tree?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    top_names = {n["name"] for n in tree.json()["nodes"]}
    assert "todelete.txt" not in top_names
    # Gitea saw a "delete" commit message (history retained per R-200-175).
    all_commits = [
        c
        for (_owner, repo), bucket in fake_gitea.commits.items()
        if repo == "proj-src"
        for c in bucket
    ]
    assert any("source — delete todelete.txt" in c.message for c in all_commits)


async def test_delete_source_file_404_when_missing(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, _svc, _g, run_id = source_app
    async with _client(app) as c:
        resp = await c.delete(
            f"/api/v1/projects/proj-src/source/file/ghost.py?run_id={run_id}",
            headers=_HEADERS_EDITOR,
        )
    assert resp.status_code == 404


async def test_delete_source_file_403_for_viewer(
    source_app: tuple[FastAPI, ArtifactsService, _FakeGiteaClient, str],
) -> None:
    app, svc, _g, run_id = source_app
    await svc.put_file(
        run_id=run_id,
        project_id="proj-src",
        tenant_id="tenant-src",
        relative_path="readonly.txt",
        data=b"x\n",
    )
    async with _client(app) as c:
        resp = await c.delete(
            f"/api/v1/projects/proj-src/source/file/readonly.txt?run_id={run_id}",
            headers=_HEADERS_VIEWER,
        )
    assert resp.status_code == 403
