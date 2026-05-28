# =============================================================================
# File: gitea_client.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c2_auth/gitea_client.py
# Description: Async httpx client for the bundled Gitea backend
#              (R-200-140..145). Surface kept minimal — exactly what
#              C2 needs to provision a project's repo + service
#              account synchronously on `POST /api/v1/projects` :
#                - create_user(username, password, email) — idempotent
#                - create_repo(owner, name, description) — idempotent
#                - delete_user(username) — used by rollback paths
#                - delete_repo(owner, name) — same
#
#              Authentication : HTTP Basic with the root admin
#              creds bootstrapped by `gitea_init` (R-200-141). Prod
#              overlays SHALL switch to per-deployment tokens stored
#              in a vault (Q-100-020).
#
#              v2 (2026-05-21): `get_file_at_ref` — fetch a file's raw
#              bytes at a specific commit SHA/branch (R-200-147 version
#              history). Backs the "view a previous revision" UX : the
#              file tree lists a doc's commit history and loads its
#              content at the chosen ref.
#
# @relation implements:R-200-140
# @relation implements:R-200-141
# @relation implements:R-200-147
# =============================================================================

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime

import httpx


class GiteaError(RuntimeError):
    """Raised on non-recoverable Gitea API failures. The C2 service
    layer wraps these into HTTP exceptions so the project-creation
    flow can roll back cleanly."""


@dataclass(frozen=True, slots=True)
class GiteaUser:
    """Subset of the Gitea user record we surface back to the caller.
    Only the fields C2 acts on are kept — `id`, `login`, and the
    timestamps live in Gitea exclusively."""

    login: str
    email: str


@dataclass(frozen=True, slots=True)
class GiteaRepo:
    """Subset of the Gitea repo record. `clone_url` is the HTTPS
    clone URL the UX exposes on `ProjectPublic.git_repo_url`
    (R-200-142). `ssh_url` is intentionally NOT exposed — SSH is
    disabled in the bundled instance (compose : DISABLE_SSH)."""

    full_name: str  # e.g. "tenant-test/project-test"
    clone_url: str
    private: bool


@dataclass(frozen=True, slots=True)
class GiteaCommit:
    """One commit returned by `list_commits`. v1 surface : sha,
    message, author identity, committed_at. Diff stats / parent shas
    are deferred to a future pass (the UX currently just renders a
    chronological list)."""

    sha: str
    message: str
    author_name: str
    author_email: str
    committed_at: datetime


class GiteaClient:
    """Async client for the bundled Gitea backend.

    All methods are idempotent — a re-run of project creation after a
    partial failure SHALL converge to the same state without
    duplicate-key errors. Idempotency is achieved by treating
    `409 Conflict` ("user already exists" / "repo already exists")
    as success when the existing record matches our intent.
    """

    def __init__(
        self,
        base_url: str,
        admin_username: str,
        admin_password: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        # Stripping the trailing slash avoids double-slash artefacts
        # in `urljoin` calls. The httpx client builds requests with
        # `base_url + path` so a single trailing slash is fine, but
        # consistency simplifies log inspection.
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(admin_username, admin_password)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Health probe — used by C2's lifespan to bail out early on a
    # broken Gitea (rather than fail the first project creation).
    # ------------------------------------------------------------------

    async def healthy(self) -> bool:
        try:
            r = await self._client.get(
                f"{self._base_url}/api/v1/version", auth=self._auth,
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    async def create_user(
        self, *, username: str, password: str, email: str,
    ) -> GiteaUser:
        """Create the service-account user for one project. Gitea's
        admin API accepts `must_change_password=False` so the
        platform-controlled password sticks (no first-login prompt).
        Re-creating an existing user returns 422 ; we treat it as
        idempotent and return the existing record."""
        payload = {
            "username": username,
            "password": password,
            "email": email,
            "must_change_password": False,
            # `source_id` 0 = built-in auth source (Gitea internal users).
            "source_id": 0,
            "send_notify": False,
        }
        r = await self._client.post(
            f"{self._base_url}/api/v1/admin/users",
            json=payload,
            auth=self._auth,
        )
        if r.status_code == 201:
            data = r.json()
            return GiteaUser(login=data["login"], email=data["email"])
        if r.status_code == 422:
            # Likely "user already exists" — confirm by GET.
            existing = await self.get_user(username)
            if existing is not None:
                return existing
        raise GiteaError(
            f"create_user({username!r}) failed: {r.status_code} {r.text[:200]}",
        )

    async def get_user(self, username: str) -> GiteaUser | None:
        r = await self._client.get(
            f"{self._base_url}/api/v1/users/{username}",
            auth=self._auth,
        )
        if r.status_code == 200:
            data = r.json()
            return GiteaUser(login=data["login"], email=data["email"])
        if r.status_code == 404:
            return None
        raise GiteaError(
            f"get_user({username!r}) failed: {r.status_code} {r.text[:200]}",
        )

    async def delete_user(self, username: str) -> bool:
        """Soft-best-effort delete. Returns True on 204, False on
        404, raises on other failures."""
        r = await self._client.delete(
            f"{self._base_url}/api/v1/admin/users/{username}",
            auth=self._auth,
            # `purge=true` removes the user's repos too so a rollback
            # cleans up cleanly even if create_repo succeeded before
            # the failure point.
            params={"purge": "true"},
        )
        if r.status_code == 204:
            return True
        if r.status_code == 404:
            return False
        raise GiteaError(
            f"delete_user({username!r}) failed: {r.status_code} {r.text[:200]}",
        )

    # ------------------------------------------------------------------
    # Repo CRUD (admin-mode : creates the repo under the SPECIFIED
    # user, not the caller — Gitea's `POST /admin/users/{u}/repos`).
    # ------------------------------------------------------------------

    async def create_repo(
        self,
        *,
        owner: str,
        name: str,
        description: str = "",
        private: bool = True,
    ) -> GiteaRepo:
        """Create a private repo owned by the service-account user.
        Idempotent on 409 Conflict (repo already exists) — returns
        the existing record."""
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "auto_init": True,  # adds an empty README + main branch
            "default_branch": "main",
        }
        r = await self._client.post(
            f"{self._base_url}/api/v1/admin/users/{owner}/repos",
            json=payload,
            auth=self._auth,
        )
        if r.status_code == 201:
            data = r.json()
            return GiteaRepo(
                full_name=data["full_name"],
                clone_url=data["clone_url"],
                private=data.get("private", True),
            )
        if r.status_code == 409:
            existing = await self.get_repo(owner=owner, name=name)
            if existing is not None:
                return existing
        raise GiteaError(
            f"create_repo({owner}/{name}) failed: "
            f"{r.status_code} {r.text[:200]}",
        )

    async def get_repo(self, *, owner: str, name: str) -> GiteaRepo | None:
        r = await self._client.get(
            f"{self._base_url}/api/v1/repos/{owner}/{name}",
            auth=self._auth,
        )
        if r.status_code == 200:
            data = r.json()
            return GiteaRepo(
                full_name=data["full_name"],
                clone_url=data["clone_url"],
                private=data.get("private", True),
            )
        if r.status_code == 404:
            return None
        raise GiteaError(
            f"get_repo({owner}/{name}) failed: {r.status_code} {r.text[:200]}",
        )

    async def delete_repo(self, *, owner: str, name: str) -> bool:
        r = await self._client.delete(
            f"{self._base_url}/api/v1/repos/{owner}/{name}",
            auth=self._auth,
        )
        if r.status_code == 204:
            return True
        if r.status_code == 404:
            return False
        raise GiteaError(
            f"delete_repo({owner}/{name}) failed: "
            f"{r.status_code} {r.text[:200]}",
        )

    # ------------------------------------------------------------------
    # File contents (R-200-146) — used by C4 to push artifacts.
    # ------------------------------------------------------------------

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
        """Create or update a file under `path` in the repo. Gitea's
        Contents API requires a `sha` parameter on UPDATE but rejects
        it on CREATE — we probe the existing file via GET first and
        switch to PUT-with-sha on hit, POST on miss. This handles
        re-runs of the seeder where the same path is rewritten across
        runs."""
        body: dict[str, str | bool] = {
            "branch": branch,
            "content": base64.b64encode(content).decode("ascii"),
            "message": message,
        }
        # Probe existing file ; 200 → sha, 404 → no file yet.
        existing = await self._client.get(
            f"{self._base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
            auth=self._auth,
            params={"ref": branch},
        )
        if existing.status_code == 200:
            body["sha"] = existing.json()["sha"]
            r = await self._client.put(
                f"{self._base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
                json=body,
                auth=self._auth,
            )
        elif existing.status_code == 404:
            r = await self._client.post(
                f"{self._base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
                json=body,
                auth=self._auth,
            )
        else:
            raise GiteaError(
                f"create_or_update_file({owner}/{repo}, {path!r}) probe "
                f"failed: {existing.status_code} {existing.text[:200]}",
            )
        if r.status_code not in (200, 201):
            raise GiteaError(
                f"create_or_update_file({owner}/{repo}, {path!r}) failed: "
                f"{r.status_code} {r.text[:200]}",
            )

    # ------------------------------------------------------------------
    # Commits listing (R-200-147) — read-only proxy.
    # ------------------------------------------------------------------

    async def list_commits(
        self,
        *,
        owner: str,
        repo: str,
        page: int = 1,
        limit: int = 50,
        path: str | None = None,
    ) -> list[GiteaCommit]:
        """Return up to `limit` commits, most recent first. Gitea's
        API uses 1-based pagination ; we mirror that to keep the
        proxy a thin layer. An empty repo returns `[]` ; the caller
        SHALL handle the empty state in the UX.

        `path` (optional) restricts results to commits touching that
        file path, used by the source-file metadata endpoint (R-200-173)
        to recover the last commit on a given file."""
        params: dict[str, str | int] = {
            "page": page, "limit": limit, "stat": "false", "files": "false",
        }
        if path is not None:
            params["path"] = path
        r = await self._client.get(
            f"{self._base_url}/api/v1/repos/{owner}/{repo}/commits",
            auth=self._auth,
            params=params,
        )
        if r.status_code == 404:
            # Empty repo or wrong owner/name : surface empty list
            # rather than raising — the UX renders "no commits yet"
            # the same way regardless of root cause.
            return []
        if r.status_code != 200:
            raise GiteaError(
                f"list_commits({owner}/{repo}) failed: "
                f"{r.status_code} {r.text[:200]}",
            )
        out: list[GiteaCommit] = []
        for entry in r.json():
            commit = entry.get("commit", {})
            author = commit.get("author", {})
            committed_at_str = author.get("date", "")
            try:
                # Gitea returns ISO-8601 with timezone. Strip the
                # trailing `Z` Python's fromisoformat doesn't accept
                # before 3.11 (we're 3.13 but defensive anyway).
                committed_at = datetime.fromisoformat(
                    committed_at_str.replace("Z", "+00:00"),
                )
            except ValueError:
                # Malformed timestamp — keep going with `now` so the
                # UX doesn't crash on a single bad entry.
                committed_at = datetime.now()
            out.append(
                GiteaCommit(
                    sha=entry.get("sha", ""),
                    message=commit.get("message", ""),
                    author_name=author.get("name", ""),
                    author_email=author.get("email", ""),
                    committed_at=committed_at,
                )
            )
        return out

    async def get_file_at_ref(
        self, *, owner: str, repo: str, path: str, ref: str,
    ) -> bytes | None:
        """Return the raw bytes of `path` as it existed at commit `ref`
        (a SHA or branch name), or None if the file did not exist at
        that ref. Backs the version-history viewer (R-200-147).

        Gitea's Contents API returns `{content: <base64>, encoding:
        "base64", ...}` for a file. A directory path returns a JSON
        list (no `content`) — treated as "not a file" → None."""
        r = await self._client.get(
            f"{self._base_url}/api/v1/repos/{owner}/{repo}/contents/{path}",
            auth=self._auth,
            params={"ref": ref},
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise GiteaError(
                f"get_file_at_ref({owner}/{repo}, {path!r}@{ref}) failed: "
                f"{r.status_code} {r.text[:200]}",
            )
        payload = r.json()
        if not isinstance(payload, dict) or payload.get("encoding") != "base64":
            # Directory listing or an unexpected shape — not a file.
            return None
        return base64.b64decode(payload.get("content", ""))
