# =============================================================================
# File: artifacts_service.py
# Version: 7
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_service.py
# Description: Facade for the project-artifacts surface. Bridges the
#              Arango run metadata (R-200-132) and the MinIO blob
#              adapter (artifacts_storage.py). Enforces the
#              tenant-scoped invariant : a run's `tenant_id` SHALL
#              match the X-Tenant-Id of the caller ; mismatches map
#              to 404 (R-200-132 — no detail leak).
#
#              Two surfaces :
#                - Read API (router) : list_runs / get_tree / get_blob.
#                - Write API (seeder + future C4 pipeline hook) :
#                  create_run + put_file. The router does NOT expose
#                  these — only the seeder script and the C4 service
#                  call them.
#
#              v4 (2026-05-20) : Tranche B §5.17 — operator-driven
#              live-docs CRUD (`mkdir_document`, `rename_document`,
#              `move_document`) beyond the LLM tool-loop. Atomicity
#              follows R-200-162 (put-then-delete with WARN-and-continue
#              on orphan cleanup). Gitea push best-effort per R-200-155.
#
#              v5 (2026-05-21) : per-file version for live-docs. Each
#              chat-driven write carries the assistant turn id (forwarded
#              as `X-Turn-Id` by C3, embedded in the commit message as
#              `[turn:<id>]`) ; `get_tree` derives `ArtifactNode.version`
#              for the live-docs run by counting DISTINCT turn ids in
#              each file's Gitea history (one bump per AI response that
#              touched the file, R-200-147 versioning proxy).
#
#              v6 (2026-05-21) : version-history viewer (R-200-147).
#              `list_commits` gains a `path` filter (per-file revision
#              list) and `read_document_at_ref` returns a live-docs
#              document's content at a specific commit SHA (from Gitea,
#              the only store keeping history).
#
#              v7 (2026-05-21) : `write_document` returns the resulting
#              per-file `version` (via `_live_docs_version_for_path`,
#              shared with the tree annotation) so the chat can render a
#              versioned "Open in working area (vN)" link (#5).
#
# @relation implements:R-200-131
# @relation implements:R-200-132
# @relation implements:R-200-133
# @relation implements:R-200-153
# @relation implements:R-200-154
# @relation implements:R-200-155
# @relation implements:R-200-160
# @relation implements:R-200-161
# @relation implements:R-200-162
# @relation implements:R-200-163
# @relation implements:R-200-164
# =============================================================================

from __future__ import annotations

import asyncio
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from ay_platform_core.c2_auth.gitea_client import GiteaClient, GiteaError
from ay_platform_core.c4_orchestrator.artifacts_models import (
    ArtifactNode,
    ArtifactRunPublic,
    ArtifactRunStatus,
    ArtifactTree,
)
from ay_platform_core.c4_orchestrator.artifacts_storage import (
    ArtifactBlob,
    ArtifactStorage,
)
from ay_platform_core.c4_orchestrator.db.repository import OrchestratorRepository


def _build_recursive_tree(
    entries: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    """Build a recursive `[{name, kind, path, size?, children?}]` tree
    from a flat MinIO listing. Folders carry `children` ; files carry
    `size_bytes`. Output is alpha-sorted within each level, folders
    first then files (VSCode convention). Used by the source-files
    tree endpoint (R-200-170). The shape mirrors the spec exactly so
    UX consumers can render without an extra normalisation step."""

    class _Folder:
        __slots__ = ("files", "name", "path", "subdirs")

        def __init__(self, path: str, name: str) -> None:
            self.path = path
            self.name = name
            self.subdirs: dict[str, _Folder] = {}
            self.files: list[tuple[str, int]] = []

    root = _Folder(path="", name="")
    for rel_path, size in entries:
        parts = [p for p in rel_path.split("/") if p]
        if not parts:
            continue
        # Skip `.keep` markers from the visible tree but use them
        # to ensure the parent directory shows up even when empty.
        if parts[-1] == ".keep" and len(parts) > 1:
            cursor = root
            for i, part in enumerate(parts[:-1]):
                key = part
                if key not in cursor.subdirs:
                    sub_path = "/".join(parts[: i + 1])
                    cursor.subdirs[key] = _Folder(sub_path, part)
                cursor = cursor.subdirs[key]
            continue
        cursor = root
        for i, part in enumerate(parts[:-1]):
            if part not in cursor.subdirs:
                sub_path = "/".join(parts[: i + 1])
                cursor.subdirs[part] = _Folder(sub_path, part)
            cursor = cursor.subdirs[part]
        cursor.files.append((parts[-1], size))

    def _emit(folder: _Folder) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in sorted(folder.subdirs.keys()):
            sub = folder.subdirs[name]
            out.append({
                "name": name,
                "kind": "dir",
                "path": sub.path,
                "children": _emit(sub),
            })
        for name, size in sorted(folder.files, key=lambda x: x[0]):
            file_path = f"{folder.path}/{name}" if folder.path else name
            out.append({
                "name": name,
                "kind": "file",
                "path": file_path,
                "size_bytes": size,
            })
        return out

    return _emit(root)


def _validate_doc_path(path: str) -> None:
    """Pre-flight check on a live-docs path argument before any I/O.
    Mirrors the validation `ArtifactStorage._object_name` enforces, so
    bad shapes (leading `/`, `..`, backslashes, dot segments) fail at
    the service-method boundary with a clear 400 rather than deep
    inside the put / list call stack. R-200-130 / R-200-163."""
    if not isinstance(path, str):  # defensive — Pydantic should prevent this
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path must be a string",
        )
    cleaned = path.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path SHALL NOT be empty",
        )
    if cleaned.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path SHALL NOT start with '/'",
        )
    if "\\" in cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path SHALL use POSIX forward slashes",
        )
    parts = cleaned.strip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path contains forbidden segments ('..', '.', or empty)",
        )


class ArtifactsService:
    """Read + write API for the artifacts surface. The router consumes
    the read methods ; the seeder + future C4 pipeline consume the
    write methods. Tenant guard is enforced uniformly here so the
    router stays minimal."""

    def __init__(
        self,
        repo: OrchestratorRepository,
        storage: ArtifactStorage,
        gitea: GiteaClient | None = None,
    ) -> None:
        self._repo = repo
        self._storage = storage
        # Optional Gitea client — when set, `mark_completed` pushes
        # every file in the run to the project's Gitea repo
        # (R-200-146). None disables the push (legacy stack without
        # Gitea ; artifacts stay in MinIO only).
        self._gitea = gitea

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    async def list_runs(
        self, *, project_id: str, tenant_id: str,
    ) -> list[ArtifactRunPublic]:
        rows = await self._repo.list_artifact_runs(tenant_id, project_id)
        return [_row_to_public(r) for r in rows]

    async def get_tree(
        self, *, run_id: str, project_id: str, tenant_id: str,
    ) -> ArtifactTree:
        run = await self._load_run_or_404(
            run_id=run_id, project_id=project_id, tenant_id=tenant_id,
        )
        try:
            raw = await self._storage.list_tree(
                tenant_id=run["tenant_id"],
                project_id=run["project_id"],
                run_id=run["_key"],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"storage backend error: {exc}",
            ) from exc
        nodes = [_blob_to_node(path, size) for path, size in raw]
        # Stable sort (alphabetical on path) so the UX tree is
        # deterministic across reloads.
        nodes.sort(key=lambda n: n.path)
        # Per-file version is a live-docs concern only (D-015) : the
        # chat-direct DocGen path tags its commits with the AI-response
        # id, so we can batch the revision count per response. Other
        # runs leave `version` as None (the UX shows no badge).
        if run["_key"] == self.LIVE_DOCS_RUN_ID:
            await self._annotate_live_docs_versions(
                nodes=nodes,
                project_id=run["project_id"],
                tenant_id=run["tenant_id"],
            )
        return ArtifactTree(run_id=run["_key"], nodes=nodes)

    async def get_blob(
        self,
        *,
        run_id: str,
        project_id: str,
        tenant_id: str,
        relative_path: str,
    ) -> ArtifactBlob:
        run = await self._load_run_or_404(
            run_id=run_id, project_id=project_id, tenant_id=tenant_id,
        )
        try:
            return await self._storage.get_blob(
                tenant_id=run["tenant_id"],
                project_id=run["project_id"],
                run_id=run["_key"],
                relative_path=relative_path,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"file {relative_path!r} not found in run",
            ) from exc
        except ValueError as exc:
            # Path-shape violation : `..`, leading `/`, backslashes.
            # 400 — the client built a bad request, not a missing file.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"storage backend error: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Write API (seeder + future C4 pipeline hook)
    # ------------------------------------------------------------------

    async def create_run(
        self,
        *,
        project_id: str,
        tenant_id: str,
        label: str | None = None,
        status_: ArtifactRunStatus = ArtifactRunStatus.RUNNING,
        run_id: str | None = None,
    ) -> str:
        """Create a fresh artifact run row in Arango. Returns the
        run_id. The blob layout under MinIO is created lazily on the
        first `put_file` call (`make_bucket` is idempotent at startup).
        Used by the seeder and (eventually) by the C4 pipeline hook
        that materialises generated files."""
        rid = run_id or str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        doc = {
            "_key": rid,
            "run_id": rid,
            "project_id": project_id,
            "tenant_id": tenant_id,
            "started_at": now,
            "completed_at": None,
            "status": status_.value,
            "file_count": 0,
            "total_bytes": 0,
            "label": label,
        }
        await self._repo.upsert_artifact_run(doc)
        return rid

    async def put_file(
        self,
        *,
        run_id: str,
        project_id: str,
        tenant_id: str,
        relative_path: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Persist one file under a run's MinIO prefix AND bump the
        run metadata's `file_count` + `total_bytes`. Idempotent on
        replays (overwrites the same key, recomputes the run totals
        from the listing rather than naive incrementing — pricey but
        bulletproof for the seeder use case)."""
        if content_type is None:
            content_type = (
                mimetypes.guess_type(relative_path)[0]
                or "application/octet-stream"
            )
        await self._storage.put_blob(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            relative_path=relative_path,
            data=data,
            content_type=content_type,
        )
        # Recompute totals from the MinIO listing — keeps the run
        # metadata consistent even if the same path is written twice.
        entries = await self._storage.list_tree(
            tenant_id=tenant_id, project_id=project_id, run_id=run_id,
        )
        existing = await self._repo.get_artifact_run(run_id) or {}
        existing.update(
            {
                "_key": run_id,
                "run_id": run_id,
                "project_id": project_id,
                "tenant_id": tenant_id,
                "file_count": len(entries),
                "total_bytes": sum(s for _, s in entries),
            }
        )
        # Default the started_at / status fields when the run was not
        # explicitly created via `create_run` first (defensive).
        existing.setdefault("started_at", datetime.now(UTC).isoformat())
        existing.setdefault("status", ArtifactRunStatus.RUNNING.value)
        existing.setdefault("completed_at", None)
        existing.setdefault("label", None)
        await self._repo.upsert_artifact_run(existing)

    async def mark_completed(
        self, *, run_id: str, status_: ArtifactRunStatus,
    ) -> None:
        """Flip a run's status + stamp completed_at, then push every
        file under the run's MinIO prefix to the project's Gitea repo
        (R-200-146). The push is best-effort : a Gitea failure logs a
        WARNING but does NOT roll back the Arango state. Called by
        the seeder at end of seeding ; the future C4 pipeline will
        call this the same way at run completion."""
        doc = await self._repo.get_artifact_run(run_id)
        if doc is None:
            raise RuntimeError(f"artifact run {run_id!r} not found")
        doc["status"] = status_.value
        doc["completed_at"] = datetime.now(UTC).isoformat()
        await self._repo.upsert_artifact_run(doc)
        # Best-effort Gitea push. Only fires when both the client is
        # wired AND the run reached `completed` (failed runs aren't
        # pushed — Gitea would otherwise mix half-baked output with
        # successful runs).
        if self._gitea is not None and status_ is ArtifactRunStatus.COMPLETED:
            await self._best_effort_push_to_gitea(doc)

    async def _best_effort_push_to_gitea(self, run_doc: dict[str, Any]) -> None:
        """Push every file under the run's MinIO prefix to the project's
        Gitea repo. The Gitea path mirrors the MinIO relative_path
        verbatim ; each file is its own commit (R-200-146). Failures
        log a WARNING and return — MinIO stays the source of truth."""
        import logging  # noqa: PLC0415 — keep import local to the rarely-taken path

        log = logging.getLogger("c4_orchestrator.artifacts")
        assert self._gitea is not None  # narrowed by caller
        tenant_id = str(run_doc["tenant_id"])
        project_id = str(run_doc["project_id"])
        run_id = str(run_doc["_key"])
        try:
            entries = await self._storage.list_tree(
                tenant_id=tenant_id, project_id=project_id, run_id=run_id,
            )
        except Exception as exc:
            log.warning(
                "gitea push (run=%s): failed to list MinIO tree: %s",
                run_id, exc,
            )
            return
        # Owner = the service-account user provisioned by C2 at project
        # creation (R-200-141). Compute deterministically from
        # (tenant, project) so we don't need to read c2_project_secrets.
        owner = f"svc-{tenant_id}-{project_id}"
        for relative_path, _size in entries:
            try:
                blob = await self._storage.get_blob(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=run_id,
                    relative_path=relative_path,
                )
            except Exception as exc:
                log.warning(
                    "gitea push (run=%s, path=%s): MinIO read failed: %s",
                    run_id, relative_path, exc,
                )
                continue
            try:
                await self._gitea.create_or_update_file(
                    owner=owner,
                    repo=project_id,
                    path=relative_path,
                    content=blob.data,
                    message=f"run {run_id} — {relative_path}",
                )
            except GiteaError as exc:
                log.warning(
                    "gitea push (run=%s, path=%s) failed: %s",
                    run_id, relative_path, exc,
                )
                # Continue with the rest of the files — partial push
                # is better than no push.

    # ------------------------------------------------------------------
    # Commit listing (R-200-147) — read-only proxy to Gitea.
    # ------------------------------------------------------------------

    async def list_commits(
        self,
        *,
        project_id: str,
        tenant_id: str,
        page: int = 1,
        path: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a paginated list of commits for the project's Gitea
        repo. Returns dicts (not a Pydantic model) so the router can
        serialise straight into the wire schema declared in
        artifacts_models. Empty when Gitea is not wired or the repo
        is empty.

        `path` (optional) restricts the list to commits that touched
        that file — the per-file revision history backing the
        "view a previous version" UX (R-200-147)."""
        if self._gitea is None:
            return []
        owner = f"svc-{tenant_id}-{project_id}"
        commits = await self._gitea.list_commits(
            owner=owner, repo=project_id, page=page, limit=50, path=path,
        )
        return [
            {
                "sha": c.sha,
                "message": c.message,
                "author_name": c.author_name,
                "author_email": c.author_email,
                "committed_at": c.committed_at,
            }
            for c in commits
        ]

    async def read_document_at_ref(
        self, *, project_id: str, tenant_id: str, path: str, ref: str,
    ) -> ArtifactBlob:
        """Read a live-docs document as it existed at commit `ref`
        (R-200-147 history viewer). Content comes from Gitea (the only
        store that keeps history — MinIO holds the latest only). 404 if
        the file did not exist at that ref, 400 on a malformed path,
        501 when Gitea is not wired, 502 on a backend error."""
        _validate_doc_path(path)
        clean = path.strip("/")
        if self._gitea is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail="version history unavailable (Gitea not configured)",
            )
        owner = f"svc-{tenant_id}-{project_id}"
        try:
            data = await self._gitea.get_file_at_ref(
                owner=owner, repo=project_id, path=clean, ref=ref,
            )
        except GiteaError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"gitea backend error: {exc}",
            ) from exc
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"document {clean!r} not found at ref {ref!r}",
            )
        mime, _ = mimetypes.guess_type(clean)
        return ArtifactBlob(
            data=data, content_type=mime or "application/octet-stream",
        )

    # ------------------------------------------------------------------
    # Chat-direct document API (D-015) — the DocGen v1 path. The
    # conversation's tool calls land here. All documents for a project
    # live under one perpetual `live-docs` run (status=RUNNING, never
    # `mark_completed`). Each write triggers an immediate single-file
    # Gitea push (incremental, not batch-on-complete like R-200-146).
    # ------------------------------------------------------------------

    LIVE_DOCS_RUN_ID = "live-docs"

    async def ensure_live_docs_run(
        self, *, project_id: str, tenant_id: str,
    ) -> str:
        """Idempotently ensure the per-project `live-docs` artifact run
        exists. Returns its run_id (always `live-docs`). The run is
        never completed — it is the perpetual document corpus."""
        existing = await self._repo.get_artifact_run(self.LIVE_DOCS_RUN_ID)
        if (
            existing is not None
            and existing.get("project_id") == project_id
            and existing.get("tenant_id") == tenant_id
        ):
            return self.LIVE_DOCS_RUN_ID
        if existing is not None:
            # The deterministic id is already taken by another
            # (tenant, project). That violates the one-live-run-per-id
            # assumption — surface loudly rather than silently cross
            # tenants.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="live-docs run id already bound to another project",
            )
        await self.create_run(
            project_id=project_id,
            tenant_id=tenant_id,
            run_id=self.LIVE_DOCS_RUN_ID,
            label="Live documents",
            status_=ArtifactRunStatus.RUNNING,
        )
        return self.LIVE_DOCS_RUN_ID

    async def list_documents(
        self, *, project_id: str, tenant_id: str,
    ) -> list[dict[str, Any]]:
        """List every document path in the project's live-docs run.
        Returns dicts `{path, size_bytes}` ; empty list when the run
        doesn't exist yet (no documents created so far)."""
        run = await self._repo.get_artifact_run(self.LIVE_DOCS_RUN_ID)
        if (
            run is None
            or run.get("project_id") != project_id
            or run.get("tenant_id") != tenant_id
        ):
            return []
        entries = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=self.LIVE_DOCS_RUN_ID,
        )
        return [{"path": p, "size_bytes": s} for p, s in entries]

    async def read_document(
        self, *, project_id: str, tenant_id: str, path: str,
    ) -> ArtifactBlob:
        """Read one document. 404 when the live-docs run or the path
        is missing ; 400 on a malformed path (`..`, leading `/`)."""
        await self._load_run_or_404(
            run_id=self.LIVE_DOCS_RUN_ID,
            project_id=project_id,
            tenant_id=tenant_id,
        )
        try:
            return await self._storage.get_blob(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=self.LIVE_DOCS_RUN_ID,
                relative_path=path,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"document {path!r} not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    async def write_document(
        self,
        *,
        project_id: str,
        tenant_id: str,
        path: str,
        content: str,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or overwrite a document. Ensures the live-docs run
        exists, writes the blob, then best-effort single-file Gitea
        push (incremental — one commit per write). Returns
        `{path, size_bytes}`. 400 on a malformed path.

        `turn_id` (the C3 assistant-response id forwarded via
        `X-Turn-Id`) is embedded in the commit message as `[turn:<id>]`
        so `get_tree` can batch the per-file version by AI response
        (all writes from one response share the id → one version bump)."""
        await self.ensure_live_docs_run(
            project_id=project_id, tenant_id=tenant_id,
        )
        data = content.encode("utf-8")
        try:
            await self.put_file(
                run_id=self.LIVE_DOCS_RUN_ID,
                project_id=project_id,
                tenant_id=tenant_id,
                relative_path=path,
                data=data,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        await self._push_one_doc_to_gitea(
            project_id=project_id,
            tenant_id=tenant_id,
            path=path,
            data=data,
            message=_docgen_commit_message(path, turn_id),
        )
        # Per-file version AFTER the push (R-200-147) so the chat can
        # render a versioned "Open in working area (vN)" link. Best-
        # effort : None when Gitea is unavailable.
        version = await self._live_docs_version_for_path(
            project_id=project_id, tenant_id=tenant_id, path=path,
        )
        return {"path": path, "size_bytes": len(data), "version": version}

    async def delete_document(
        self, *, project_id: str, tenant_id: str, path: str,
    ) -> None:
        """Delete a document from MinIO. 404 when missing. Gitea
        history is intentionally NOT rewritten — the deleted doc
        survives in git history (audit trail ; D-015 tech-debt note)."""
        await self._load_run_or_404(
            run_id=self.LIVE_DOCS_RUN_ID,
            project_id=project_id,
            tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=self.LIVE_DOCS_RUN_ID,
        )
        if path not in {p for p, _ in entries}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"document {path!r} not found",
            )
        try:
            await self._storage.delete_blob(
                tenant_id=tenant_id,
                project_id=project_id,
                run_id=self.LIVE_DOCS_RUN_ID,
                relative_path=path,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        # Recompute run totals after removal so the UX file-count stays
        # accurate.
        remaining = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=self.LIVE_DOCS_RUN_ID,
        )
        run = await self._repo.get_artifact_run(self.LIVE_DOCS_RUN_ID)
        if run is not None:
            run["file_count"] = len(remaining)
            run["total_bytes"] = sum(s for _, s in remaining)
            await self._repo.upsert_artifact_run(run)

    # ------------------------------------------------------------------
    # Live-docs operator-driven CRUD beyond the LLM tool-loop (§5.17)
    # R-200-160..164
    # ------------------------------------------------------------------

    async def mkdir_document(
        self, *, project_id: str, tenant_id: str, path: str,
    ) -> dict[str, Any]:
        """Materialise an empty directory by writing a zero-byte `.keep`
        marker at `<path>/.keep`. 409 if any blob already exists under
        the directory prefix. Triggers a best-effort Gitea push.
        Returns `{path}`."""
        _validate_doc_path(path)
        clean = path.strip("/")
        if not clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path SHALL NOT be empty",
            )
        await self.ensure_live_docs_run(
            project_id=project_id, tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=self.LIVE_DOCS_RUN_ID,
        )
        prefix = f"{clean}/"
        if any(p == clean or p.startswith(prefix) for p, _ in entries):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"path {clean!r} already exists",
            )
        keep_path = f"{prefix}.keep"
        try:
            await self.put_file(
                run_id=self.LIVE_DOCS_RUN_ID,
                project_id=project_id,
                tenant_id=tenant_id,
                relative_path=keep_path,
                data=b"",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        await self._push_one_doc_to_gitea(
            project_id=project_id,
            tenant_id=tenant_id,
            path=keep_path,
            data=b"",
            message=f"docgen — mkdir {clean}",
        )
        return {"path": clean}

    async def rename_document(
        self,
        *,
        project_id: str,
        tenant_id: str,
        from_path: str,
        to_path: str,
    ) -> dict[str, Any]:
        """Atomically (at the service-method level — R-200-162) rename a
        file or directory. 404 if source missing ; 409 if target exists ;
        400 on cycles or same path. Triggers one best-effort Gitea push."""
        _validate_doc_path(from_path)
        _validate_doc_path(to_path)
        src = from_path.strip("/")
        dst = to_path.strip("/")
        if not src or not dst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="paths SHALL NOT be empty",
            )
        if src == dst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_path == to_path",
            )
        if dst.startswith(f"{src}/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="to_path is a descendant of from_path (cycle)",
            )
        moves = await self._build_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            src=src,
            dst=dst,
        )
        await self._apply_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            moves=moves,
            commit_message=f"docgen — rename {src} -> {dst}",
        )
        return {"from_path": src, "to_path": dst, "moved": len(moves)}

    async def move_document(
        self,
        *,
        project_id: str,
        tenant_id: str,
        from_path: str,
        to_dir: str,
    ) -> dict[str, Any]:
        """Move a file or directory under a different directory. Reduces
        to the rename primitive with the target path computed as
        `<to_dir>/<basename(from_path)>`."""
        _validate_doc_path(from_path)
        _validate_doc_path(to_dir)
        src = from_path.strip("/")
        target_dir = to_dir.strip("/")
        if not src:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_path SHALL NOT be empty",
            )
        basename = src.rsplit("/", 1)[-1]
        dst = f"{target_dir}/{basename}" if target_dir else basename
        if src == dst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_path is already under to_dir",
            )
        if dst.startswith(f"{src}/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="to_dir is a descendant of from_path (cycle)",
            )
        moves = await self._build_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            src=src,
            dst=dst,
        )
        await self._apply_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            moves=moves,
            commit_message=f"docgen — move {src} -> {target_dir or '/'}",
        )
        return {"from_path": src, "to_dir": target_dir, "moved": len(moves)}

    async def _build_move_plan(
        self,
        *,
        project_id: str,
        tenant_id: str,
        src: str,
        dst: str,
        run_id: str | None = None,
    ) -> list[tuple[str, str]]:
        """Enumerate the `(old_key, new_key)` pairs the rename/move must
        execute. Raises 404 if `src` matches neither a file nor a
        directory prefix ; raises 409 if any destination already
        exists. `run_id` defaults to the live-docs run ; pass an
        explicit run to target the source-files surface (§5.18)."""
        target_run = run_id or self.LIVE_DOCS_RUN_ID
        await self._load_run_or_404(
            run_id=target_run,
            project_id=project_id,
            tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=target_run,
        )
        existing_paths = {p for p, _ in entries}
        src_prefix = f"{src}/"
        is_file = src in existing_paths
        sub_files = [p for p in existing_paths if p.startswith(src_prefix)]
        if not is_file and not sub_files:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"path {src!r} not found",
            )
        moves: list[tuple[str, str]] = []
        if is_file:
            moves.append((src, dst))
        else:
            dst_prefix = f"{dst}/"
            for p in sub_files:
                tail = p[len(src_prefix):]
                moves.append((p, f"{dst_prefix}{tail}"))
        # Collision check : any destination already present (and not
        # being itself moved out) blocks the whole operation.
        sources = {old for old, _ in moves}
        for _, new in moves:
            if new in existing_paths and new not in sources:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"target {new!r} already exists",
                )
        return moves

    async def _apply_move_plan(
        self,
        *,
        project_id: str,
        tenant_id: str,
        moves: list[tuple[str, str]],
        commit_message: str,
        run_id: str | None = None,
    ) -> None:
        """Execute a move plan : copy each blob to its destination, then
        delete the source. Failure to delete the source after a
        successful write logs a WARNING and yields the success path with
        an orphan left behind (R-200-162). A single Gitea commit closes
        the operation best-effort. `run_id` defaults to the live-docs
        run ; pass an explicit run to operate on the source-files
        surface (§5.18)."""
        import logging  # noqa: PLC0415 — cold path

        log = logging.getLogger("c4_orchestrator.artifacts")
        target_run = run_id or self.LIVE_DOCS_RUN_ID
        for old, new in moves:
            try:
                blob = await self._storage.get_blob(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=target_run,
                    relative_path=old,
                )
                await self._storage.put_blob(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=target_run,
                    relative_path=new,
                    data=blob.data,
                    content_type=blob.content_type,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            try:
                await self._storage.delete_blob(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=target_run,
                    relative_path=old,
                )
            except Exception as exc:  # best-effort orphan cleanup
                log.warning(
                    "artifacts move (run=%s): failed to delete source %s "
                    "after copy to %s : %s (orphan left behind)",
                    target_run, old, new, exc,
                )
        # Refresh run totals after the move so file_count matches MinIO.
        remaining = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=target_run,
        )
        run = await self._repo.get_artifact_run(target_run)
        if run is not None:
            run["file_count"] = len(remaining)
            run["total_bytes"] = sum(s for _, s in remaining)
            await self._repo.upsert_artifact_run(run)
        # Best-effort Gitea push : one commit per move. Implementation
        # detail — we push the newest file written above as the visible
        # commit; for batched directory moves this gives a single
        # marker commit on the project repo (operators see "moved X
        # files" in the log).
        if moves:
            _, sample_new = moves[-1]
            try:
                blob = await self._storage.get_blob(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=target_run,
                    relative_path=sample_new,
                )
                await self._push_one_doc_to_gitea(
                    project_id=project_id,
                    tenant_id=tenant_id,
                    path=sample_new,
                    data=blob.data,
                    message=commit_message,
                )
            except Exception as exc:  # best-effort push
                log.warning(
                    "artifacts move (run=%s): gitea push (%s) failed : %s",
                    target_run, commit_message, exc,
                )

    # ------------------------------------------------------------------
    # Source-files surface : tree projection + structural ops + metadata
    # (§5.18 — R-200-170..174). Scoped to one artifact run_id at a time
    # (Q-200-017 deferral on project-wide aggregation).
    # ------------------------------------------------------------------

    async def get_source_tree(
        self, *, project_id: str, tenant_id: str, run_id: str,
    ) -> list[dict[str, Any]]:
        """Return the recursive source-files tree for one artifact run.
        Shape : `[ {name, kind, path, size?, modified_at?, children?}, … ]`
        — folders carry `children`, files carry `size_bytes`. Computed
        at request time from the MinIO listing (R-200-170)."""
        await self._load_run_or_404(
            run_id=run_id,
            project_id=project_id,
            tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
        )
        return _build_recursive_tree(entries)

    async def mkdir_source(
        self, *, project_id: str, tenant_id: str, run_id: str, path: str,
    ) -> dict[str, Any]:
        """Same semantics as mkdir_document but scoped to an arbitrary
        source-files artifact run (R-200-171). Materialises a `.keep`
        marker, 409 if path exists, best-effort Gitea push."""
        _validate_doc_path(path)
        clean = path.strip("/")
        if not clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path SHALL NOT be empty",
            )
        await self._load_run_or_404(
            run_id=run_id, project_id=project_id, tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id, project_id=project_id, run_id=run_id,
        )
        prefix = f"{clean}/"
        if any(p == clean or p.startswith(prefix) for p, _ in entries):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"path {clean!r} already exists",
            )
        keep_path = f"{prefix}.keep"
        try:
            await self.put_file(
                run_id=run_id,
                project_id=project_id,
                tenant_id=tenant_id,
                relative_path=keep_path,
                data=b"",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        await self._push_one_doc_to_gitea(
            project_id=project_id,
            tenant_id=tenant_id,
            path=keep_path,
            data=b"",
            message=f"source — mkdir {clean}",
        )
        return {"path": clean}

    async def rename_source(
        self,
        *,
        project_id: str,
        tenant_id: str,
        run_id: str,
        from_path: str,
        to_path: str,
    ) -> dict[str, Any]:
        """Same atomic-at-the-method-level rename as rename_document
        but scoped to a non-live-docs source-files run (R-200-171)."""
        _validate_doc_path(from_path)
        _validate_doc_path(to_path)
        src = from_path.strip("/")
        dst = to_path.strip("/")
        if not src or not dst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="paths SHALL NOT be empty",
            )
        if src == dst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_path == to_path",
            )
        if dst.startswith(f"{src}/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="to_path is a descendant of from_path (cycle)",
            )
        moves = await self._build_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            src=src,
            dst=dst,
            run_id=run_id,
        )
        await self._apply_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            moves=moves,
            commit_message=f"source — rename {src} -> {dst}",
            run_id=run_id,
        )
        return {"from_path": src, "to_path": dst, "moved": len(moves)}

    async def move_source(
        self,
        *,
        project_id: str,
        tenant_id: str,
        run_id: str,
        from_path: str,
        to_dir: str,
    ) -> dict[str, Any]:
        _validate_doc_path(from_path)
        _validate_doc_path(to_dir)
        src = from_path.strip("/")
        target_dir = to_dir.strip("/")
        if not src:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_path SHALL NOT be empty",
            )
        basename = src.rsplit("/", 1)[-1]
        dst = f"{target_dir}/{basename}" if target_dir else basename
        if src == dst:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_path is already under to_dir",
            )
        if dst.startswith(f"{src}/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="to_dir is a descendant of from_path (cycle)",
            )
        moves = await self._build_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            src=src,
            dst=dst,
            run_id=run_id,
        )
        await self._apply_move_plan(
            project_id=project_id,
            tenant_id=tenant_id,
            moves=moves,
            commit_message=f"source — move {src} -> {target_dir or '/'}",
            run_id=run_id,
        )
        return {"from_path": src, "to_dir": target_dir, "moved": len(moves)}

    async def delete_source_file(
        self,
        *,
        project_id: str,
        tenant_id: str,
        run_id: str,
        path: str,
    ) -> None:
        """Delete one source file (R-200-175). 404 when missing ; 400
        on malformed path. Best-effort Gitea push (history retained,
        same audit choice as R-200-155 for live-docs)."""
        _validate_doc_path(path)
        clean = path.strip("/")
        if not clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="path SHALL NOT be empty",
            )
        await self._load_run_or_404(
            run_id=run_id, project_id=project_id, tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id, project_id=project_id, run_id=run_id,
        )
        if clean not in {p for p, _ in entries}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"source file {clean!r} not found in run {run_id!r}",
            )
        try:
            await self._storage.delete_blob(
                tenant_id=tenant_id, project_id=project_id, run_id=run_id,
                relative_path=clean,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        # Refresh run totals so the UX file count stays accurate.
        remaining = await self._storage.list_tree(
            tenant_id=tenant_id, project_id=project_id, run_id=run_id,
        )
        run = await self._repo.get_artifact_run(run_id)
        if run is not None:
            run["file_count"] = len(remaining)
            run["total_bytes"] = sum(s for _, s in remaining)
            await self._repo.upsert_artifact_run(run)
        # Best-effort Gitea push : delete-marker commit, history retained
        # (mirror of R-200-155 audit choice).
        await self._push_one_doc_to_gitea(
            project_id=project_id,
            tenant_id=tenant_id,
            path=clean,
            data=b"",
            message=f"source — delete {clean}",
        )

    async def get_source_file_meta(
        self, *, project_id: str, tenant_id: str, run_id: str, path: str,
    ) -> dict[str, Any]:
        """Return metadata for one source-files entry (R-200-173).
        `last_commit_*` are best-effort from Gitea ; `kg_indexed` is
        deferred (Q-200-018) and emitted as None in v1."""
        _validate_doc_path(path)
        clean = path.strip("/")
        await self._load_run_or_404(
            run_id=run_id, project_id=project_id, tenant_id=tenant_id,
        )
        entries = await self._storage.list_tree(
            tenant_id=tenant_id, project_id=project_id, run_id=run_id,
        )
        match = next((p_s for p_s in entries if p_s[0] == clean), None)
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"path {clean!r} not found in run {run_id!r}",
            )
        size = int(match[1])
        mime, _ = mimetypes.guess_type(clean)
        out: dict[str, Any] = {
            "path": clean,
            "size": size,
            "mime_type": mime or "application/octet-stream",
            "modified_at": None,
            "last_commit_sha": None,
            "last_commit_message": None,
            "last_commit_author": None,
            "kg_indexed": None,
        }
        # Best-effort Gitea lookup. If the client raises (auth, network,
        # missing repo) we silently drop the commit fields — the
        # operator still gets size + mime so the panel renders.
        if self._gitea is not None:
            owner = f"svc-{tenant_id}-{project_id}"
            try:
                commits = await self._gitea.list_commits(
                    owner=owner, repo=project_id, page=1, limit=1,
                    path=clean,
                )
                if commits:
                    last = commits[0]
                    out["last_commit_sha"] = last.sha
                    out["last_commit_message"] = last.message
                    out["last_commit_author"] = last.author_name
                    out["modified_at"] = last.committed_at.isoformat()
            except GiteaError:
                pass
        return out

    async def _push_one_doc_to_gitea(
        self,
        *,
        project_id: str,
        tenant_id: str,
        path: str,
        data: bytes,
        message: str | None = None,
    ) -> None:
        """Best-effort single-file Gitea push for the chat-direct
        document path (D-015). Mirrors R-200-146 semantics (root admin
        owner, one commit per file, WARN-and-continue on failure) but
        scoped to one file instead of the whole run.

        `message` overrides the default commit message — used by the
        Tranche B structural ops (mkdir/rename/move) which carry their
        own intent label (R-200-161 / R-200-162)."""
        if self._gitea is None:
            return
        import logging  # noqa: PLC0415 — keep import on the cold path

        log = logging.getLogger("c4_orchestrator.artifacts")
        owner = f"svc-{tenant_id}-{project_id}"
        try:
            await self._gitea.create_or_update_file(
                owner=owner,
                repo=project_id,
                path=path,
                content=data,
                message=message or f"docgen — {path}",
            )
        except GiteaError as exc:
            log.warning(
                "gitea push (live-docs, path=%s) failed: %s", path, exc,
            )

    async def _annotate_live_docs_versions(
        self,
        *,
        nodes: list[ArtifactNode],
        project_id: str,
        tenant_id: str,
    ) -> None:
        """Set `node.version` on each file node from the Gitea history,
        batched per AI response. Best-effort : when Gitea is absent or a
        per-file lookup fails, `version` stays None (no UX badge). The
        per-file `list_commits` calls run concurrently so a tree with N
        docs costs one round-trip's latency, not N sequential ones."""
        if self._gitea is None:
            return
        files = [n for n in nodes if n.kind == "file"]

        async def _one(node: ArtifactNode) -> None:
            node.version = await self._live_docs_version_for_path(
                project_id=project_id, tenant_id=tenant_id, path=node.path,
            )

        await asyncio.gather(*(_one(n) for n in files))

    async def _live_docs_version_for_path(
        self, *, project_id: str, tenant_id: str, path: str,
    ) -> int | None:
        """Per-file version (count of distinct AI-response turn ids in
        the file's Gitea history, R-200-147). None when Gitea is absent
        or the lookup fails — the caller renders no version badge/link."""
        if self._gitea is None:
            return None
        owner = f"svc-{tenant_id}-{project_id}"
        try:
            commits = await self._gitea.list_commits(
                owner=owner, repo=project_id, page=1, limit=100, path=path,
            )
        except GiteaError:
            return None
        return _version_from_commit_messages([c.message for c in commits])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_run_or_404(
        self, *, run_id: str, project_id: str, tenant_id: str,
    ) -> dict[str, Any]:
        """Fetch the run document, asserting it belongs to the
        (tenant, project) tuple. Mismatch maps to 404 per R-200-132
        (avoid leaking existence to a foreign tenant)."""
        doc = await self._repo.get_artifact_run(run_id)
        if (
            doc is None
            or doc.get("tenant_id") != tenant_id
            or doc.get("project_id") != project_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"artifact run {run_id!r} not found",
            )
        return doc


def _row_to_public(row: dict[str, Any]) -> ArtifactRunPublic:
    """Map a raw Arango row to the public schema. `_key` is the
    canonical id ; `started_at` / `completed_at` are ISO strings that
    Pydantic v2 parses automatically into datetimes."""
    return ArtifactRunPublic(
        run_id=str(row["_key"]),
        project_id=str(row["project_id"]),
        tenant_id=str(row["tenant_id"]),
        started_at=row["started_at"],
        completed_at=row.get("completed_at"),
        status=ArtifactRunStatus(row["status"]),
        file_count=int(row.get("file_count") or 0),
        total_bytes=int(row.get("total_bytes") or 0),
        label=row.get("label") if isinstance(row.get("label"), str) else None,
    )


# Marker embedded in live-docs commit messages so the per-file version
# can be batched by AI response. `_docgen_commit_message` writes it ;
# `_version_from_commit_messages` reads it back from `list_commits`.
_TURN_TAG_RE = re.compile(r"\[turn:([^\]]+)\]")


def _docgen_commit_message(path: str, turn_id: str | None) -> str:
    """Commit message for a chat-direct document write. Appends a
    `[turn:<id>]` marker when the AI-response id is known so the tree
    version count can group all writes from one response into a single
    revision (D-015 / R-200-147)."""
    base = f"docgen — {path}"
    if turn_id:
        return f"{base} [turn:{turn_id}]"
    return base


def _version_from_commit_messages(messages: list[str]) -> int | None:
    """Per-file version = number of DISTINCT AI-response turn ids found
    in the file's commit history. Multiple writes within one response
    share a turn id, so they collapse to one version bump (the operator
    sees one revision per response, not per individual write).

    Falls back to `1` for a file whose history carries no turn marker
    (legacy / pre-feature commits, or operator-driven structural ops) so
    an existing document still shows at least `v1`. Returns None for an
    empty history (no commits) so the UX renders no badge."""
    if not messages:
        return None
    turns: set[str] = set()
    for msg in messages:
        turns.update(_TURN_TAG_RE.findall(msg))
    return len(turns) if turns else 1


def _blob_to_node(relative_path: str, size: int) -> ArtifactNode:
    """Map a `(rel_path, size)` listing entry to the public node
    shape. MIME type is inferred from the extension — UX falls back
    to a generic icon when None."""
    mime, _ = mimetypes.guess_type(relative_path)
    # v1 surface only files ; pseudo-dirs are inferred client-side
    # from path segments.
    return ArtifactNode(
        path=relative_path,
        kind="file",
        size_bytes=size,
        mime_type=mime,
    )
