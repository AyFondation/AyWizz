# =============================================================================
# File: test_gitea_provisioning.py
# Version: 4
# Path: ay_platform_core/tests/integration/c2_auth/test_gitea_provisioning.py
# Description: Integration tests for the Gitea provisioning hook
#              (R-200-141..142). Uses a `FakeGiteaClient` stub
#              (in-memory replacement for the real httpx-backed
#              GiteaClient) so the test runs without a Gitea
#              container ; the contract verified is the C2 service
#              layer's behaviour, not the Gitea API itself (which is
#              upstream-vendor-owned).
#
#              v3 (2026-05-21): `_FakeGiteaClient.list_commits` now
#              honours the `path` filter (it records the touched path
#              per commit), matching the real client. This is needed by
#              the live-docs per-file version count (R-200-147), which
#              relies on path-scoped commit history to batch revisions
#              per file. `path=None` still returns the full repo log.
#
#              v4 (2026-05-21): `_FakeGiteaClient.get_file_at_ref`
#              records content per commit and returns the bytes a file
#              had at a given SHA — backs the "view a previous revision"
#              tests (R-200-147 history viewer).
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio
from arango import ArangoClient  # type: ignore[attr-defined]
from fastapi import FastAPI

from ay_platform_core.c2_auth.admin_router import router as c2_admin_router
from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.gitea_client import (
    GiteaCommit,
    GiteaError,
    GiteaRepo,
    GiteaUser,
)
from ay_platform_core.c2_auth.models import RBACGlobalRole
from ay_platform_core.c2_auth.projects_router import router as c2_projects_router
from ay_platform_core.c2_auth.router import router as c2_router
from ay_platform_core.c2_auth.service import AuthService
from ay_platform_core.c2_auth.service import get_service as c2_get_service
from tests.fixtures.containers import (
    ArangoEndpoint,
    cleanup_arango_database,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


# ---------------------------------------------------------------------------
# Stub GiteaClient — same surface as the real one, in-memory.
# ---------------------------------------------------------------------------


@dataclass
class _FakeGiteaClient:
    """Stand-in for the real `GiteaClient` ; records every call into
    in-memory dicts so the test can assert on the surface AuthService
    invoked. Behaviour is intentionally minimal — just enough to
    exercise the provisioning + rollback code paths AND the artifact-
    push + list-commits paths from Pass 2.2."""

    base_url: str = "http://fake-gitea:3000"
    users: dict[str, GiteaUser] = field(default_factory=dict)
    repos: dict[str, GiteaRepo] = field(default_factory=dict)
    # files[(owner, repo)] = {path: bytes} — each `create_or_update_file`
    # records the latest content per path.
    files: dict[tuple[str, str], dict[str, bytes]] = field(default_factory=dict)
    # commits[(owner, repo)] = [GiteaCommit, ...] — one commit per
    # `create_or_update_file` call, prepended (most recent first).
    commits: dict[tuple[str, str], list[GiteaCommit]] = field(default_factory=dict)
    # commit_paths[(owner, repo)] = [path, ...] — index-aligned with
    # `commits`, so `list_commits(path=...)` can filter like real Gitea.
    commit_paths: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # commit_contents[(owner, repo)] = [bytes, ...] — index-aligned with
    # `commits`, so `get_file_at_ref` can return the bytes a file had at
    # a given SHA (real Gitea keeps full history ; the fake snapshots).
    commit_contents: dict[tuple[str, str], list[bytes]] = field(default_factory=dict)
    fail_on_create_repo: bool = False
    fail_on_create_file: bool = False

    async def healthy(self) -> bool:
        return True

    async def create_user(
        self, *, username: str, password: str, email: str,
    ) -> GiteaUser:
        user = GiteaUser(login=username, email=email)
        self.users[username] = user
        return user

    async def get_user(self, username: str) -> GiteaUser | None:
        return self.users.get(username)

    async def delete_user(self, username: str) -> bool:
        if username in self.users:
            # Mirror Gitea's `purge=true` behaviour : cascade to repos.
            owned = [k for k, v in self.repos.items() if v.full_name.startswith(f"{username}/")]
            for k in owned:
                self.repos.pop(k, None)
            self.users.pop(username, None)
            return True
        return False

    async def create_repo(
        self, *, owner: str, name: str,
        description: str = "", private: bool = True,
    ) -> GiteaRepo:
        if self.fail_on_create_repo:
            raise GiteaError("simulated Gitea failure")
        full_name = f"{owner}/{name}"
        repo = GiteaRepo(
            full_name=full_name,
            clone_url=f"{self.base_url}/{full_name}.git",
            private=private,
        )
        self.repos[full_name] = repo
        return repo

    async def get_repo(self, *, owner: str, name: str) -> GiteaRepo | None:
        return self.repos.get(f"{owner}/{name}")

    async def delete_repo(self, *, owner: str, name: str) -> bool:
        return self.repos.pop(f"{owner}/{name}", None) is not None

    async def create_or_update_file(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        content: bytes,
        message: str,
        branch: str = "main",
    ) -> None:
        if self.fail_on_create_file:
            raise GiteaError("simulated Gitea file write failure")
        bucket = self.files.setdefault((owner, repo), {})
        bucket[path] = content
        # Record a synthetic commit so list_commits has something to
        # return — most recent first.
        from datetime import UTC, datetime  # noqa: PLC0415
        commit_log = self.commits.setdefault((owner, repo), [])
        commit_log.insert(
            0,
            GiteaCommit(
                sha=f"sha-{len(commit_log) + 1}",
                message=message,
                author_name="aywizz",
                author_email="aywizz@local",
                committed_at=datetime.now(UTC),
            ),
        )
        # Keep the touched path + content index-aligned with `commits`
        # so `list_commits(path=...)` filters and `get_file_at_ref`
        # returns historical bytes faithfully.
        self.commit_paths.setdefault((owner, repo), []).insert(0, path)
        self.commit_contents.setdefault((owner, repo), []).insert(0, content)

    async def list_commits(
        self,
        *,
        owner: str,
        repo: str,
        page: int = 1,
        limit: int = 50,
        path: str | None = None,
    ) -> list[GiteaCommit]:
        # `path` mirrors the real GiteaClient (R-200-173 / R-200-147).
        # When supplied, restrict to commits that touched that path
        # (index-aligned with `commit_paths`) ; otherwise return the
        # full repo log, most recent first.
        commits = list(self.commits.get((owner, repo), []))
        if path is not None:
            paths = self.commit_paths.get((owner, repo), [])
            commits = [
                c
                for c, p in zip(commits, paths, strict=False)
                if p == path
            ]
        return commits[:limit]

    async def get_file_at_ref(
        self, *, owner: str, repo: str, path: str, ref: str,
    ) -> bytes | None:
        # Walk the index-aligned commit/path/content lists (most recent
        # first) and return the content of the commit whose sha == ref
        # and which touched `path`. None when there's no such commit.
        commits = self.commits.get((owner, repo), [])
        paths = self.commit_paths.get((owner, repo), [])
        contents = self.commit_contents.get((owner, repo), [])
        for c, p, data in zip(commits, paths, contents, strict=False):
            if c.sha == ref and p == path:
                return data
        return None

    async def aclose(self) -> None:  # match real client
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def gitea_stack(
    arango_container: ArangoEndpoint,
) -> AsyncIterator[tuple[FastAPI, AuthService, _FakeGiteaClient]]:
    db_name = f"c2_gitea_{uuid.uuid4().hex[:8]}"
    sys_db = ArangoClient(hosts=arango_container.url).db(
        "_system", username="root", password=arango_container.password,
    )
    sys_db.create_database(db_name)
    repo = AuthRepository.from_config(
        arango_container.url, db_name, "root", arango_container.password,
    )
    repo._ensure_collections_sync()
    config = AuthConfig.model_validate(
        {
            "auth_mode": "local",
            "jwt_secret_key": "gitea-test-secret-32-chars-min!!!",
            "platform_environment": "testing",
        }
    )
    fake_gitea = _FakeGiteaClient()
    service = AuthService(config, repo, gitea=fake_gitea)  # type: ignore[arg-type]
    app = FastAPI()
    app.include_router(c2_router, prefix="/auth")
    app.include_router(c2_admin_router, prefix="/admin")
    app.include_router(c2_projects_router, prefix="/api/v1/projects")
    app.dependency_overrides[c2_get_service] = lambda: service
    try:
        yield app, service, fake_gitea
    finally:
        cleanup_arango_database(arango_container, db_name)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e-gitea",
    )


def _forward_auth(user_id: str, tenant_id: str, roles: tuple[str, ...]) -> dict[str, str]:
    return {
        "X-User-Id": user_id,
        "X-Tenant-Id": tenant_id,
        "X-User-Roles": ",".join(roles),
    }


# ---------------------------------------------------------------------------
# Happy path : project creation auto-provisions a Gitea repo.
# ---------------------------------------------------------------------------


async def test_create_project_provisions_gitea_repo(
    gitea_stack: tuple[FastAPI, AuthService, _FakeGiteaClient],
) -> None:
    app, service, fake_gitea = gitea_stack
    tenant_id = "tenant-gp"
    project_id = "proj-gp"

    # Pre-create the tenant via tenant_manager.
    tm_jti = f"tm-{uuid.uuid4().hex[:6]}"
    from datetime import UTC, datetime  # noqa: PLC0415

    from ay_platform_core.c2_auth.models import JWTClaims  # noqa: PLC0415
    tm_claims = JWTClaims(
        sub="u-tm", iat=int(datetime.now(tz=UTC).timestamp()),
        exp=10**12, jti=tm_jti, auth_mode="local",
        tenant_id="platform", roles=[RBACGlobalRole.TENANT_MANAGER],
    )
    tm_token = service._sign_jwt(tm_claims)
    if service._repo is not None:
        now = datetime.now(tz=UTC)
        await service._repo.insert_session(
            tm_jti, "u-tm", now, now.replace(year=now.year + 1),
        )
    async with _client(app) as c:
        r = await c.post(
            "/admin/tenants",
            headers={"Authorization": f"Bearer {tm_token}"},
            json={"tenant_id": tenant_id, "name": tenant_id},
        )
        assert r.status_code == 201, r.text

    # Create the project via admin forward-auth.
    admin_fa = _forward_auth("u-adm", tenant_id, ("admin",))
    async with _client(app) as c:
        r = await c.post(
            "/api/v1/projects",
            headers=admin_fa,
            json={"project_id": project_id, "name": "Gitea demo"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # `git_repo_url` is populated.
        assert body["git_repo_url"] is not None
        assert body["git_repo_url"].endswith(
            f"/svc-{tenant_id}-{project_id}/{project_id}.git",
        )

    # Gitea side-effects observable on the stub.
    expected_user = f"svc-{tenant_id}-{project_id}"
    assert expected_user in fake_gitea.users
    assert f"{expected_user}/{project_id}" in fake_gitea.repos

    # The secret was persisted in c2_project_secrets.
    secret = await service._repo.get_project_secret(project_id)  # type: ignore[union-attr]
    assert secret is not None
    assert secret["gitea_username"] == expected_user
    assert secret["gitea_password"]
    assert secret["gitea_repo_full_name"] == f"{expected_user}/{project_id}"


# ---------------------------------------------------------------------------
# Rollback path : Gitea create_repo fails ; project row is NOT created.
# ---------------------------------------------------------------------------


async def test_create_project_rolls_back_on_gitea_failure(
    gitea_stack: tuple[FastAPI, AuthService, _FakeGiteaClient],
) -> None:
    app, service, fake_gitea = gitea_stack
    tenant_id = "tenant-rb"
    project_id = "proj-rb"
    fake_gitea.fail_on_create_repo = True

    # Same tenant bootstrap dance as the happy-path test.
    from datetime import UTC, datetime  # noqa: PLC0415

    from ay_platform_core.c2_auth.models import JWTClaims  # noqa: PLC0415
    tm_jti = f"tm-{uuid.uuid4().hex[:6]}"
    tm_claims = JWTClaims(
        sub="u-tm", iat=int(datetime.now(tz=UTC).timestamp()),
        exp=10**12, jti=tm_jti, auth_mode="local",
        tenant_id="platform", roles=[RBACGlobalRole.TENANT_MANAGER],
    )
    tm_token = service._sign_jwt(tm_claims)
    if service._repo is not None:
        now = datetime.now(tz=UTC)
        await service._repo.insert_session(
            tm_jti, "u-tm", now, now.replace(year=now.year + 1),
        )
    async with _client(app) as c:
        await c.post(
            "/admin/tenants",
            headers={"Authorization": f"Bearer {tm_token}"},
            json={"tenant_id": tenant_id, "name": tenant_id},
        )

    admin_fa = _forward_auth("u-adm", tenant_id, ("admin",))
    async with _client(app) as c:
        r = await c.post(
            "/api/v1/projects",
            headers=admin_fa,
            json={"project_id": project_id, "name": "Should rollback"},
        )
        # 502 — git backend failed.
        assert r.status_code == 502, r.text

    # No project row was created.
    assert await service._repo.get_project(project_id) is None  # type: ignore[union-attr]
    # The half-created service-account user was purged.
    expected_user = f"svc-{tenant_id}-{project_id}"
    assert expected_user not in fake_gitea.users
    # No secret persisted.
    assert await service._repo.get_project_secret(project_id) is None  # type: ignore[union-attr]
