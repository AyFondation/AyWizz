# =============================================================================
# File: artifacts_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/artifacts_router.py
# Description: REST surface for the project-artifacts feature (Pass 1).
#              Three read-only endpoints mounted under
#              `/api/v1/projects/{pid}/artifacts/*`. Forward-auth
#              identity model (X-User-Id / X-Tenant-Id / X-User-Roles)
#              identical to C3/C5/C6/C7. `tenant_manager` is rejected
#              because artifacts are tenant content (E-100-002 v2).
#
#              Profile-agnostic : the same endpoints serve `codegen`
#              (source code) and `docgen` (rendered documents) ;
#              profile-specific labelling lives in the UX registry.
#
# @relation implements:R-200-131
# @relation implements:R-200-132
# @relation implements:R-200-133
# =============================================================================

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c4_orchestrator.artifacts_models import (
    ArtifactCommit,
    ArtifactCommitList,
    ArtifactRunList,
    ArtifactRunPublic,
    ArtifactRunStatus,
    ArtifactTree,
)
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService

router = APIRouter(tags=["artifacts"])


# ---------------------------------------------------------------------------
# Forward-auth header helpers — duplicated from `projects_router.py` instead
# of imported to keep the artifacts module's dependency graph tight.
# ---------------------------------------------------------------------------


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header missing",
        )
    return x_tenant_id


def _reject_tenant_manager(x_user_roles: str | None) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if "tenant_manager" in roles and not roles.intersection(
        ("admin", "tenant_admin"),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_manager has no access to tenant content (E-100-002 v2)",
        )


def _require_admin(x_user_roles: str | None) -> None:
    """Gate the admin-only seed endpoint to `admin` / `tenant_admin`.
    Used by the dev seeder ; the C4 pipeline writes artifacts
    in-process and doesn't go through this surface."""
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(("admin", "tenant_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin or tenant_admin role required",
        )


def _get_service(request: Request) -> ArtifactsService:
    """Retrieve the service instance injected via `app.state` at
    startup (see `main.py`). Same pattern as C3."""
    svc: ArtifactsService = request.app.state.artifacts_service
    return svc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/artifacts/runs",
    response_model=ArtifactRunList,
)
async def list_artifact_runs(
    project_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> ArtifactRunList:
    _reject_tenant_manager(x_user_roles)
    runs = await service.list_runs(
        project_id=project_id, tenant_id=tenant_id,
    )
    return ArtifactRunList(runs=runs)


@router.get(
    "/api/v1/projects/{project_id}/artifacts/runs/{run_id}/tree",
    response_model=ArtifactTree,
)
async def get_artifact_tree(
    project_id: str,
    run_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> ArtifactTree:
    _reject_tenant_manager(x_user_roles)
    return await service.get_tree(
        run_id=run_id, project_id=project_id, tenant_id=tenant_id,
    )


@router.get(
    "/api/v1/projects/{project_id}/artifacts/runs/{run_id}/blob",
)
async def get_artifact_blob(
    project_id: str,
    run_id: str,
    path: str,
    download: int = 0,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> Response:
    """Stream the file content back. `Content-Disposition: inline` by
    default so the UX can render the file in Monaco / via an
    `<iframe>` for PDFs ; `?download=1` flips to `attachment` so a
    direct browser navigation triggers a download. The byte stream
    is fully read in memory for Pass 1 — switch to a streaming
    response when file sizes warrant (see `artifacts_storage.py`)."""
    _reject_tenant_manager(x_user_roles)
    blob = await service.get_blob(
        run_id=run_id,
        project_id=project_id,
        tenant_id=tenant_id,
        relative_path=path,
    )
    # Filename for Content-Disposition — last segment of the path.
    filename = path.rsplit("/", 1)[-1] or "artifact"
    disposition = "attachment" if download else "inline"
    return Response(
        content=blob.data,
        media_type=blob.content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            # Prevent intermediate caches from serving a stale blob
            # across runs (run_id is part of the path so URLs are
            # immutable per run, but be defensive against proxy
            # misbehaviour).
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


# ---------------------------------------------------------------------------
# Project versioning — commit history proxy (R-200-147)
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/git/commits",
    response_model=ArtifactCommitList,
)
async def list_project_commits(
    project_id: str,
    page: int = 1,
    path: str | None = None,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> ArtifactCommitList:
    """Read-only proxy over the project's Gitea repo (R-200-145
    transparency : the UX never reaches Gitea directly). Returns the
    most-recent-first commits list, paginated server-side at 50 per
    page. Empty list when Gitea is not wired or the repo has no
    commits yet — the UX renders 'no versions yet' uniformly.

    `path` (optional query) restricts the list to commits that touched
    that file — the per-file revision history backing the
    "view a previous version" UX (R-200-147)."""
    _reject_tenant_manager(x_user_roles)
    raw = await service.list_commits(
        project_id=project_id, tenant_id=tenant_id, page=page, path=path,
    )
    return ArtifactCommitList(
        commits=[ArtifactCommit(**entry) for entry in raw],
        page=page,
    )


# ---------------------------------------------------------------------------
# Admin seed endpoint (dev-only ; admin-gated)
#
# Used by `scripts/seed_demo_ux.py` to pre-populate a demo run with
# sample files (README.md + hello.py + …) so the UX has something to
# browse without waiting for a real C4 pipeline run. Kept on the
# regular `c4-artifacts` router so it benefits from the same Traefik
# routing rule ; gated to `admin` / `tenant_admin` and rejected for
# `tenant_manager` (content-blind). NOT a public surface.
# ---------------------------------------------------------------------------


class _SeedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    content_b64: str  # base64-encoded bytes — flat JSON keeps the seeder simple


class _SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = None
    label: str | None = None
    files: list[_SeedFile] = Field(min_length=1, max_length=200)


@router.post(
    "/api/v1/admin/projects/{project_id}/artifacts/seed",
    response_model=ArtifactRunPublic,
)
async def admin_seed_artifacts(
    project_id: str,
    body: _SeedRequest,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> ArtifactRunPublic:
    """Create an artifact run + upload every file in `body.files` +
    mark it COMPLETED. One-shot for the dev seeder ; not used by the
    real C4 pipeline (which writes artifacts in-process)."""
    _require_admin(x_user_roles)
    run_id = await service.create_run(
        project_id=project_id,
        tenant_id=tenant_id,
        label=body.label,
        run_id=body.run_id,
    )
    for f in body.files:
        try:
            data = base64.b64decode(f.content_b64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"file {f.path!r} : invalid base64 — {exc}",
            ) from exc
        await service.put_file(
            run_id=run_id,
            project_id=project_id,
            tenant_id=tenant_id,
            relative_path=f.path,
            data=data,
        )
    await service.mark_completed(
        run_id=run_id, status_=ArtifactRunStatus.COMPLETED,
    )
    # Fetch fresh state to return the canonical view (with file_count,
    # total_bytes computed from MinIO listing).
    runs = await service.list_runs(
        project_id=project_id, tenant_id=tenant_id,
    )
    for r in runs:
        if r.run_id == run_id:
            return r
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="run not found after seed — internal inconsistency",
    )
