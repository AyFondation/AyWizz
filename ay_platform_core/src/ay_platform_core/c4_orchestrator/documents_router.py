# =============================================================================
# File: documents_router.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/documents_router.py
# Description: REST surface for the chat-direct DocGen document API
#              (D-015). CRUD on the project's `live-docs` artifact run :
#                POST   /api/v1/projects/{pid}/documents       — create/overwrite
#                PUT    /api/v1/projects/{pid}/documents/{path} — update
#                GET    /api/v1/projects/{pid}/documents        — list
#                GET    /api/v1/projects/{pid}/documents/{path} — read
#                DELETE /api/v1/projects/{pid}/documents/{path} — delete
#
#              These endpoints are the v1 mutation surface invoked by
#              the C3 conversation's tool calls (Phase 2.C.2). They
#              are profile-agnostic in code ; the DocGen profile uses
#              them, the migration to the synthesis-v4 pipeline path
#              (v2) reuses the same surface from OpenHands-in-C15.
#
#              v2 (2026-05-20) : Tranche B §5.17 — operator-driven
#              structural ops :
#                POST /api/v1/projects/{pid}/documents/mkdir
#                POST /api/v1/projects/{pid}/documents/rename
#                POST /api/v1/projects/{pid}/documents/move
#              These are NOT LLM tools (Q-200-014 defers tool exposure
#              to v2). They are direct UX actions from the live-docs
#              tree right-click menu.
#
#              v3 (2026-05-21) : create/update accept an optional
#              `X-Turn-Id` header (the C3 assistant-response id). It is
#              forwarded to `write_document` and embedded in the Gitea
#              commit message so the tree's per-file version batches by
#              AI response (D-015 / R-200-147). Absent header → untagged
#              commit (operator-driven write outside the chat loop).
#
#              v4 (2026-05-21) : GET /documents/{path} accepts an
#              optional `?ref=<sha>` query — returns the document as it
#              existed at that commit (R-200-147 history viewer). Absent
#              → the current MinIO content.
#
#              v5 (2026-05-21) : `DocumentRef.version` — create/update
#              responses carry the resulting per-file version so the
#              chat renders a versioned "Open in working area (vN)" link
#              below the response (#5 / R-200-147).
#
#              Forward-auth identity model identical to artifacts_router
#              (X-User-Id / X-Tenant-Id / X-User-Roles). `tenant_manager`
#              is rejected — documents are tenant content (E-100-002 v2).
#
# @relation implements:R-200-153
# @relation implements:R-200-154
# @relation implements:R-200-155
# @relation implements:R-200-156
# @relation implements:R-200-160
# @relation implements:R-200-161
# @relation implements:R-200-162
# @relation implements:R-200-163
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c4_orchestrator.artifacts_router import (
    _get_service,
    _reject_tenant_manager,
    _require_actor,
    _require_tenant,
)
from ay_platform_core.c4_orchestrator.artifacts_service import ArtifactsService

router = APIRouter(tags=["documents"])


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class DocumentWrite(BaseModel):
    """Body of POST /documents — create or overwrite. `path` follows
    the R-200-130 convention (POSIX relative, no `..`, no leading
    `/`). `content` is the full UTF-8 text body."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    content: str


class DocumentUpdate(BaseModel):
    """Body of PUT /documents/{path} — `path` comes from the URL, so
    only the new content is in the body."""

    model_config = ConfigDict(extra="forbid")

    content: str


class DocumentRef(BaseModel):
    """One row of the documents listing + the create/update response.
    `version` is populated on create/update (the per-file revision count
    after the write, R-200-147) so the chat can render a versioned
    "Open in working area (vN)" link ; it is None on the listing."""

    model_config = ConfigDict(extra="forbid")

    path: str
    size_bytes: int = Field(ge=0)
    version: int | None = Field(default=None, ge=1)


class DocumentList(BaseModel):
    """Wrapper for the documents listing — forward-compat room for
    pagination cursors, same reasoning as `ArtifactRunList`."""

    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentRef]


class DocumentMkdir(BaseModel):
    """Body of POST /documents/mkdir — creates a `.keep` marker at
    `<path>/.keep` per R-200-161."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)


class DocumentRename(BaseModel):
    """Body of POST /documents/rename — pure path change. Works on
    files AND directories per R-200-162."""

    model_config = ConfigDict(extra="forbid")

    from_path: str = Field(min_length=1, max_length=512)
    to_path: str = Field(min_length=1, max_length=512)


class DocumentMove(BaseModel):
    """Body of POST /documents/move — relocate under a different
    directory. Target path = `<to_dir>/<basename(from_path)>`."""

    model_config = ConfigDict(extra="forbid")

    from_path: str = Field(min_length=1, max_length=512)
    # `to_dir` MAY be empty to mean "move to root" — the service
    # composes the destination as just the basename in that case.
    to_dir: str = Field(max_length=512)


class DocumentStructuralOpResult(BaseModel):
    """Common response shape for mkdir/rename/move. `moved` counts the
    underlying blobs touched (1 for a file, N for a directory)."""

    model_config = ConfigDict(extra="forbid")

    from_path: str | None = None
    to_path: str | None = None
    to_dir: str | None = None
    path: str | None = None
    moved: int | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/projects/{project_id}/documents",
    response_model=DocumentRef,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    project_id: str,
    body: DocumentWrite,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    x_turn_id: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> DocumentRef:
    """Create or overwrite a document. Idempotent on the same path
    (overwrites). Triggers an incremental Gitea push (one commit).
    `X-Turn-Id` (the C3 response id) batches the per-file version."""
    _reject_tenant_manager(x_user_roles)
    result = await service.write_document(
        project_id=project_id,
        tenant_id=tenant_id,
        path=body.path,
        content=body.content,
        turn_id=x_turn_id,
    )
    return DocumentRef(**result)


@router.put(
    "/api/v1/projects/{project_id}/documents/{path:path}",
    response_model=DocumentRef,
)
async def update_document(
    project_id: str,
    path: str,
    body: DocumentUpdate,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    x_turn_id: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> DocumentRef:
    """Overwrite an existing document at `{path}`. Same effect as POST
    with that path — separate verb so the conversation tool surface
    can distinguish create vs update intent in its audit trail.
    `X-Turn-Id` (the C3 response id) batches the per-file version."""
    _reject_tenant_manager(x_user_roles)
    result = await service.write_document(
        project_id=project_id,
        tenant_id=tenant_id,
        path=path,
        content=body.content,
        turn_id=x_turn_id,
    )
    return DocumentRef(**result)


@router.get(
    "/api/v1/projects/{project_id}/documents",
    response_model=DocumentList,
)
async def list_documents(
    project_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> DocumentList:
    """List every document path in the project's live-docs corpus.
    Empty list when no document has been created yet."""
    _reject_tenant_manager(x_user_roles)
    rows = await service.list_documents(
        project_id=project_id, tenant_id=tenant_id,
    )
    return DocumentList(documents=[DocumentRef(**r) for r in rows])


@router.get("/api/v1/projects/{project_id}/documents/{path:path}")
async def read_document(
    project_id: str,
    path: str,
    ref: str | None = Query(default=None),
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> Response:
    """Stream a document's content back. 404 when missing, 400 on a
    malformed path. `ref` (optional commit SHA) returns the document as
    it existed at that revision (R-200-147 history viewer) ; absent →
    the current content from MinIO."""
    _reject_tenant_manager(x_user_roles)
    if ref:
        blob = await service.read_document_at_ref(
            project_id=project_id, tenant_id=tenant_id, path=path, ref=ref,
        )
    else:
        blob = await service.read_document(
            project_id=project_id, tenant_id=tenant_id, path=path,
        )
    filename = path.rsplit("/", 1)[-1] or "document"
    return Response(
        content=blob.data,
        media_type=blob.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=0, must-revalidate",
        },
    )


@router.delete(
    "/api/v1/projects/{project_id}/documents/{path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    project_id: str,
    path: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> Response:
    """Delete a document from MinIO. 404 when the path is unknown.
    Gitea history is intentionally retained (audit ; D-015)."""
    _reject_tenant_manager(x_user_roles)
    await service.delete_document(
        project_id=project_id, tenant_id=tenant_id, path=path,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/projects/{project_id}/documents/mkdir",
    response_model=DocumentStructuralOpResult,
    status_code=status.HTTP_201_CREATED,
)
async def mkdir_document(
    project_id: str,
    body: DocumentMkdir,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> DocumentStructuralOpResult:
    """Materialise an empty directory by writing a `.keep` marker
    (R-200-161). 409 if the path already exists."""
    _reject_tenant_manager(x_user_roles)
    result = await service.mkdir_document(
        project_id=project_id, tenant_id=tenant_id, path=body.path,
    )
    return DocumentStructuralOpResult(**result)


@router.post(
    "/api/v1/projects/{project_id}/documents/rename",
    response_model=DocumentStructuralOpResult,
)
async def rename_document(
    project_id: str,
    body: DocumentRename,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> DocumentStructuralOpResult:
    """Rename a file or directory atomically at the service-method
    level (R-200-162). 404 on missing source, 409 on existing target,
    400 on self-rename or traversal cycle."""
    _reject_tenant_manager(x_user_roles)
    result = await service.rename_document(
        project_id=project_id,
        tenant_id=tenant_id,
        from_path=body.from_path,
        to_path=body.to_path,
    )
    return DocumentStructuralOpResult(**result)


@router.post(
    "/api/v1/projects/{project_id}/documents/move",
    response_model=DocumentStructuralOpResult,
)
async def move_document(
    project_id: str,
    body: DocumentMove,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ArtifactsService = Depends(_get_service),
) -> DocumentStructuralOpResult:
    """Move a file or directory under a different directory. Reduces
    to `rename` with target = `<to_dir>/<basename(from_path)>`."""
    _reject_tenant_manager(x_user_roles)
    result = await service.move_document(
        project_id=project_id,
        tenant_id=tenant_id,
        from_path=body.from_path,
        to_dir=body.to_dir,
    )
    return DocumentStructuralOpResult(**result)
