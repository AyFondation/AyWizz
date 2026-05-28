# =============================================================================
# File: source_router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/source_router.py
# Description: REST surface for the project source-files tree + ops
#              (§5.18 — R-200-170..174). Provides a tree-shaped UX over
#              an artifact run's MinIO contents, plus operator-driven
#              mkdir / rename / move, plus per-file metadata. RBAC :
#              project_editor+ (per R-200-171 — stricter than live-docs
#              because source files materialise into Gitea history).
#
#              Scoped to one artifact run_id at a time via a required
#              query parameter (Q-200-017 — project-wide aggregation
#              deferred ; the UI's existing run picker chooses the run).
#
# @relation implements:R-200-170
# @relation implements:R-200-171
# @relation implements:R-200-172
# @relation implements:R-200-173
# @relation implements:R-200-174
# =============================================================================

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c4_orchestrator.artifacts_router import (
    _get_service,
    _reject_tenant_manager,
    _require_actor,
    _require_tenant,
)
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService

router = APIRouter(tags=["source"])


def _require_editor_role(x_user_roles: str | None) -> None:
    """Editor+ role gate per R-200-171. `project_owner`, `project_editor`,
    or `admin` accepted ; everything else (incl. project_viewer) → 403."""
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    accepted = {"project_owner", "project_editor", "admin"}
    if not roles.intersection(accepted):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="requires one of: project_owner, project_editor, admin",
        )


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class SourceTreeNode(BaseModel):
    """Recursive tree node returned by GET /source/tree (R-200-170)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["file", "dir"]
    path: str
    size_bytes: int | None = None
    children: list[SourceTreeNode] | None = None


class SourceTreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    truncated: bool = False
    nodes: list[SourceTreeNode]


class SourceMkdir(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)


class SourceRename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_path: str = Field(min_length=1, max_length=512)
    to_path: str = Field(min_length=1, max_length=512)


class SourceMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_path: str = Field(min_length=1, max_length=512)
    to_dir: str = Field(max_length=512)


class SourceStructuralOpResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    from_path: str | None = None
    to_path: str | None = None
    to_dir: str | None = None
    path: str | None = None
    moved: int | None = None


class SourceFileMeta(BaseModel):
    """Response of GET /source/file/{path}/meta (R-200-173)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    mime_type: str
    modified_at: str | None = None
    last_commit_sha: str | None = None
    last_commit_message: str | None = None
    last_commit_author: str | None = None
    kg_indexed: bool | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/source/tree",
    response_model=SourceTreeResponse,
)
async def read_source_tree(
    project_id: str,
    run_id: str = Query(..., description="Artifact run_id to project."),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> SourceTreeResponse:
    """Return the source-files tree for one run as a recursive node
    list (R-200-170). The 5 MB truncation marker is not exercised in
    v1 — practical project trees stay well below that ceiling."""
    _reject_tenant_manager(x_user_roles)
    raw = await service.get_source_tree(
        project_id=project_id, tenant_id=tenant_id, run_id=run_id,
    )
    return SourceTreeResponse(
        run_id=run_id,
        nodes=[SourceTreeNode.model_validate(n) for n in raw],
    )


@router.post(
    "/api/v1/projects/{project_id}/source/mkdir",
    response_model=SourceStructuralOpResult,
    status_code=status.HTTP_201_CREATED,
)
async def mkdir_source(
    project_id: str,
    body: SourceMkdir,
    run_id: str = Query(..., description="Artifact run_id to mutate."),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> SourceStructuralOpResult:
    """Create an empty directory by writing a `.keep` marker. 409 if
    the path already exists. Editor+ RBAC (R-200-171)."""
    _reject_tenant_manager(x_user_roles)
    _require_editor_role(x_user_roles)
    result: dict[str, Any] = await service.mkdir_source(
        project_id=project_id, tenant_id=tenant_id, run_id=run_id, path=body.path,
    )
    return SourceStructuralOpResult(run_id=run_id, **result)


@router.post(
    "/api/v1/projects/{project_id}/source/rename",
    response_model=SourceStructuralOpResult,
)
async def rename_source(
    project_id: str,
    body: SourceRename,
    run_id: str = Query(..., description="Artifact run_id to mutate."),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> SourceStructuralOpResult:
    _reject_tenant_manager(x_user_roles)
    _require_editor_role(x_user_roles)
    result: dict[str, Any] = await service.rename_source(
        project_id=project_id,
        tenant_id=tenant_id,
        run_id=run_id,
        from_path=body.from_path,
        to_path=body.to_path,
    )
    return SourceStructuralOpResult(run_id=run_id, **result)


@router.post(
    "/api/v1/projects/{project_id}/source/move",
    response_model=SourceStructuralOpResult,
)
async def move_source(
    project_id: str,
    body: SourceMove,
    run_id: str = Query(..., description="Artifact run_id to mutate."),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> SourceStructuralOpResult:
    _reject_tenant_manager(x_user_roles)
    _require_editor_role(x_user_roles)
    result: dict[str, Any] = await service.move_source(
        project_id=project_id,
        tenant_id=tenant_id,
        run_id=run_id,
        from_path=body.from_path,
        to_dir=body.to_dir,
    )
    return SourceStructuralOpResult(run_id=run_id, **result)


@router.delete(
    "/api/v1/projects/{project_id}/source/file/{path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_source_file(
    project_id: str,
    path: str,
    run_id: str = Query(..., description="Artifact run_id to mutate."),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> Response:
    """Delete one source file (R-200-175). Editor+ RBAC."""
    _reject_tenant_manager(x_user_roles)
    _require_editor_role(x_user_roles)
    if not path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path SHALL NOT be empty",
        )
    await service.delete_source_file(
        project_id=project_id, tenant_id=tenant_id, run_id=run_id, path=path,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v1/projects/{project_id}/source/file/{path:path}/meta",
    response_model=SourceFileMeta,
)
async def read_source_file_meta(
    project_id: str,
    path: str,
    run_id: str = Query(..., description="Artifact run_id to inspect."),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> SourceFileMeta:
    """Return metadata for one source-files entry (R-200-173). Reader
    RBAC (any project member, tenant_manager rejected)."""
    _reject_tenant_manager(x_user_roles)
    if not path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path SHALL NOT be empty",
        )
    meta = await service.get_source_file_meta(
        project_id=project_id, tenant_id=tenant_id, run_id=run_id, path=path,
    )
    return SourceFileMeta(**meta)
