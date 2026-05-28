# =============================================================================
# File: test_artifacts_api.py
# Version: 2
# Path: ay_platform_core/tests/integration/c4_orchestrator/test_artifacts_api.py
# Description: Integration tests for the project-artifacts surface
#              (R-200-131..133, R-200-146..147). Exercises the 3 read
#              endpoints + the admin seed endpoint against real ArangoDB
#              AND real MinIO testcontainers, end-to-end through the
#              FastAPI router, plus the Pass 2.2 Gitea push + commits
#              proxy via a stubbed Gitea client.
# =============================================================================

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI
from minio import Minio

from ay_platform_core.c4_orchestrator.artifacts_router import (
    router as artifacts_router,
)
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService
from ay_platform_core.c4_orchestrator.artifacts_storage import ArtifactStorage
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
)
from tests.integration.c2_auth.test_gitea_provisioning import _FakeGiteaClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


_HEADERS = {
    "X-User-Id": "alice",
    "X-Tenant-Id": "tenant-art",
    "X-User-Roles": "project_editor,admin",
}
_HEADERS_OTHER_TENANT = {
    "X-User-Id": "bob",
    "X-Tenant-Id": "tenant-other",
    "X-User-Roles": "project_editor,admin",
}
_HEADERS_TENANT_MANAGER = {
    "X-User-Id": "tm",
    "X-Tenant-Id": "tenant-art",
    "X-User-Roles": "tenant_manager",
}


@pytest_asyncio.fixture(scope="function")
async def artifacts_app(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[FastAPI]:
    db_name = f"c4_art_{uuid.uuid4().hex[:8]}"
    bucket = f"artbucket-{uuid.uuid4().hex[:8]}"
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
    service = ArtifactsService(repo=repo, storage=storage)

    app = FastAPI()
    app.include_router(artifacts_router)
    app.state.artifacts_service = service
    try:
        yield app
    finally:
        cleanup_arango_database(arango_container, db_name)


@pytest_asyncio.fixture(scope="function")
async def artifacts_app_with_gitea(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[tuple[FastAPI, _FakeGiteaClient]]:
    """Same as `artifacts_app`, but wires a `_FakeGiteaClient` into the
    service so `mark_completed` triggers the best-effort push to Gitea
    (R-200-146) and the commits proxy (R-200-147) has data to return."""
    db_name = f"c4_artg_{uuid.uuid4().hex[:8]}"
    bucket = f"artbucket-{uuid.uuid4().hex[:8]}"
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

    app = FastAPI()
    app.include_router(artifacts_router)
    app.state.artifacts_service = service
    try:
        yield app, fake_gitea
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-art",
    )


# ---------------------------------------------------------------------------
# Read endpoints — happy path + tenant guard
# ---------------------------------------------------------------------------


async def test_seed_then_list_runs_returns_the_demo_run(
    artifacts_app: FastAPI,
) -> None:
    """End-to-end : seed via admin endpoint, then list runs, then
    fetch the tree + a blob. Asserts each layer's contract."""
    async with _client(artifacts_app) as c:
        # Seed a run with 2 files.
        seed = await c.post(
            "/api/v1/admin/projects/proj-x/artifacts/seed",
            headers=_HEADERS,
            json={
                "run_id": "demo-run-001",
                "label": "Demo",
                "files": [
                    {
                        "path": "README.md",
                        "content_b64": base64.b64encode(b"# Hello\n").decode(),
                    },
                    {
                        "path": "src/main.py",
                        "content_b64": base64.b64encode(
                            b'print("hi")\n',
                        ).decode(),
                    },
                ],
            },
        )
        assert seed.status_code == 200, seed.text
        body = seed.json()
        assert body["run_id"] == "demo-run-001"
        assert body["status"] == "completed"
        assert body["file_count"] == 2
        assert body["total_bytes"] > 0

        # List runs.
        listing = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs", headers=_HEADERS,
        )
        assert listing.status_code == 200, listing.text
        runs = listing.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["run_id"] == "demo-run-001"
        assert runs[0]["label"] == "Demo"

        # Tree.
        tree = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs/demo-run-001/tree",
            headers=_HEADERS,
        )
        assert tree.status_code == 200, tree.text
        paths = {n["path"] for n in tree.json()["nodes"]}
        assert paths == {"README.md", "src/main.py"}

        # Blob inline (text) ; assert Content-Type + body.
        blob = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs/demo-run-001/blob",
            headers=_HEADERS,
            params={"path": "README.md"},
        )
        assert blob.status_code == 200, blob.text
        assert blob.text == "# Hello\n"
        assert blob.headers["content-disposition"].startswith("inline")

        # Blob with ?download=1 flips Content-Disposition to attachment.
        download = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs/demo-run-001/blob",
            headers=_HEADERS,
            params={"path": "src/main.py", "download": 1},
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-disposition"].startswith("attachment")


async def test_tenant_mismatch_returns_404(artifacts_app: FastAPI) -> None:
    """R-200-132 : a run created under tenant-art SHALL NOT be
    visible from tenant-other (404, no detail leak)."""
    async with _client(artifacts_app) as c:
        # Seed under tenant-art.
        seed = await c.post(
            "/api/v1/admin/projects/proj-x/artifacts/seed",
            headers=_HEADERS,
            json={
                "run_id": "rid-iso",
                "files": [
                    {"path": "a.txt", "content_b64": base64.b64encode(b"x").decode()},
                ],
            },
        )
        assert seed.status_code == 200

        # Read under tenant-other — listing is empty + run-specific
        # endpoints map to 404.
        listing = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs",
            headers=_HEADERS_OTHER_TENANT,
        )
        assert listing.status_code == 200
        assert listing.json()["runs"] == []

        tree = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs/rid-iso/tree",
            headers=_HEADERS_OTHER_TENANT,
        )
        assert tree.status_code == 404

        blob = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs/rid-iso/blob",
            headers=_HEADERS_OTHER_TENANT,
            params={"path": "a.txt"},
        )
        assert blob.status_code == 404


async def test_tenant_manager_rejected_on_artifacts(
    artifacts_app: FastAPI,
) -> None:
    """E-100-002 v2 : tenant_manager is content-blind. SHALL be 403
    on every artifacts endpoint, even on listing."""
    async with _client(artifacts_app) as c:
        # No seed needed — list is enough to exercise the role gate.
        listing = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs",
            headers=_HEADERS_TENANT_MANAGER,
        )
        assert listing.status_code == 403
        # Admin seed endpoint also rejects tenant_manager.
        seed = await c.post(
            "/api/v1/admin/projects/proj-x/artifacts/seed",
            headers=_HEADERS_TENANT_MANAGER,
            json={
                "files": [{"path": "x", "content_b64": base64.b64encode(b"x").decode()}],
            },
        )
        # First check is `_require_admin` which rejects tenant_manager
        # (it's not in the admin set) — 403.
        assert seed.status_code == 403


async def test_blob_path_traversal_rejected(artifacts_app: FastAPI) -> None:
    """R-200-130 : `..` and leading `/` SHALL be rejected with 400,
    not silently mapped to a different MinIO object."""
    async with _client(artifacts_app) as c:
        await c.post(
            "/api/v1/admin/projects/proj-x/artifacts/seed",
            headers=_HEADERS,
            json={
                "run_id": "rid-trav",
                "files": [
                    {"path": "a.txt", "content_b64": base64.b64encode(b"x").decode()},
                ],
            },
        )
        for bad_path in ("../escape.txt", "/etc/passwd", "a\\b.txt"):
            r = await c.get(
                "/api/v1/projects/proj-x/artifacts/runs/rid-trav/blob",
                headers=_HEADERS,
                params={"path": bad_path},
            )
            assert r.status_code == 400, f"path {bad_path!r} should be 400"


# ---------------------------------------------------------------------------
# Pass 2.2 — Gitea push on completion + commits proxy
# ---------------------------------------------------------------------------


async def test_seed_pushes_to_gitea_and_commits_visible(
    artifacts_app_with_gitea: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """R-200-146 + R-200-147 : seeding a run with files SHALL push
    every file to the project's Gitea repo at completion AND the
    platform-proxied commits endpoint SHALL surface those commits in
    the canonical wire shape (the UX never reaches Gitea directly,
    R-200-145)."""
    app, fake_gitea = artifacts_app_with_gitea
    async with _client(app) as c:
        # Seed a run with 2 files. mark_completed fires inside the
        # admin seed handler -> best-effort push to Gitea.
        seed = await c.post(
            "/api/v1/admin/projects/proj-x/artifacts/seed",
            headers=_HEADERS,
            json={
                "run_id": "demo-push-001",
                "label": "Push demo",
                "files": [
                    {
                        "path": "README.md",
                        "content_b64": base64.b64encode(b"# Hello\n").decode(),
                    },
                    {
                        "path": "src/main.py",
                        "content_b64": base64.b64encode(
                            b'print("hi")\n',
                        ).decode(),
                    },
                ],
            },
        )
        assert seed.status_code == 200, seed.text
        assert seed.json()["status"] == "completed"

        # Gitea side-effects observable on the stub. Owner is the
        # service-account name derived deterministically from
        # (tenant, project) — see ArtifactsService._best_effort_push_to_gitea.
        expected_owner = "svc-tenant-art-proj-x"
        expected_repo = "proj-x"
        key = (expected_owner, expected_repo)
        assert key in fake_gitea.files, (
            f"no push happened for {key}; files={fake_gitea.files!r}"
        )
        pushed = fake_gitea.files[key]
        assert pushed["README.md"] == b"# Hello\n"
        assert pushed["src/main.py"] == b'print("hi")\n'

        # Two commits — one per file. Order is FakeGitea's insertion
        # order (most recent first), independent of MinIO listing order.
        commits_log = fake_gitea.commits.get(key, [])
        assert len(commits_log) == 2

        # Now exercise the commits proxy endpoint.
        proxy = await c.get(
            "/api/v1/projects/proj-x/git/commits",
            headers=_HEADERS,
        )
        assert proxy.status_code == 200, proxy.text
        body = proxy.json()
        assert body["page"] == 1
        assert len(body["commits"]) == 2
        # Schema check : every commit has the 5 declared fields and
        # nothing else (ArtifactCommit has extra="forbid").
        first = body["commits"][0]
        assert set(first.keys()) == {
            "sha", "message", "author_name", "author_email", "committed_at",
        }
        # Messages mention the run + path so the UX can show them as-is.
        all_messages = {c["message"] for c in body["commits"]}
        assert any("demo-push-001" in m and "README.md" in m for m in all_messages)
        assert any(
            "demo-push-001" in m and "src/main.py" in m for m in all_messages
        )


async def test_commits_proxy_rejects_tenant_manager(
    artifacts_app_with_gitea: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """E-100-002 v2 : tenant_manager is content-blind. The commits
    proxy is project-scoped content -> SHALL be 403."""
    app, _ = artifacts_app_with_gitea
    async with _client(app) as c:
        r = await c.get(
            "/api/v1/projects/proj-x/git/commits",
            headers=_HEADERS_TENANT_MANAGER,
        )
        assert r.status_code == 403, r.text


async def test_gitea_push_failure_does_not_break_seed(
    artifacts_app_with_gitea: tuple[FastAPI, _FakeGiteaClient],
) -> None:
    """R-200-146 best-effort semantics : a Gitea failure during push
    SHALL log a warning but NOT roll back the MinIO write nor fail
    the seed endpoint. MinIO is the source of truth."""
    app, fake_gitea = artifacts_app_with_gitea
    fake_gitea.fail_on_create_file = True
    async with _client(app) as c:
        seed = await c.post(
            "/api/v1/admin/projects/proj-x/artifacts/seed",
            headers=_HEADERS,
            json={
                "run_id": "demo-push-fail",
                "files": [
                    {
                        "path": "a.txt",
                        "content_b64": base64.b64encode(b"data").decode(),
                    },
                ],
            },
        )
        # Seed still returns 200 — MinIO write happened.
        assert seed.status_code == 200, seed.text
        assert seed.json()["status"] == "completed"

        # Tree still served from MinIO regardless of Gitea outage.
        tree = await c.get(
            "/api/v1/projects/proj-x/artifacts/runs/demo-push-fail/tree",
            headers=_HEADERS,
        )
        assert tree.status_code == 200
        assert {n["path"] for n in tree.json()["nodes"]} == {"a.txt"}

        # No commits landed on Gitea since the stub raised on every
        # create_or_update_file call.
        key = ("svc-tenant-art-proj-x", "proj-x")
        assert fake_gitea.commits.get(key, []) == []
