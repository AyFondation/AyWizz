# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/router.py
# Description: FastAPI APIRouter for the C5 Requirements Service.
#              Endpoint roster per R-300-024. v2 (v1.5 upgrade) lifts the
#              stubs on reindex, markdown export, and adds a reconcile
#              admin endpoint. Import + ReqIF export + point-in-time
#              export remain 501 stubs.
#
# @relation implements:R-300-020
# @relation implements:R-300-024
# @relation implements:R-300-025
# @relation implements:R-300-027
# @relation implements:R-300-063
# @relation implements:R-300-070
# @relation implements:R-300-073
# @relation implements:R-300-084
# @relation implements:R-300-086
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from ay_platform_core.c5_requirements.models import (
    DocumentCreate,
    DocumentListResponse,
    DocumentPublic,
    DocumentReplace,
    EntityListResponse,
    EntityPublic,
    EntityUpdate,
    HistoryListResponse,
    ImportReport,
    ImportRequest,
    ReindexJob,
    RelationListResponse,
    RelationType,
    RequirementStatus,
    TailoringReport,
)
from ay_platform_core.c5_requirements.service import (
    ConsistencyError,
    ReconcileReport,
    RequirementsService,
    get_service,
)

router = APIRouter(tags=["requirements"])


# ---------------------------------------------------------------------------
# Identity — sourced from Traefik forward-auth headers (R-300-027 + A-7 of plan)
# ---------------------------------------------------------------------------


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_role(
    x_user_roles: str | None = Header(default=None),
    required: tuple[str, ...] = ("project_editor", "project_owner", "admin"),
) -> None:
    """Validate the caller holds at least one of the required roles.

    Roles arrive as a comma-separated list propagated from C2 /auth/verify.
    RBAC is enforced per R-300-027: project_editor for writes, admin for
    platform-level writes, project_owner for document deletion.
    """
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


# ---------------------------------------------------------------------------
# Document endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/requirements/documents",
    response_model=DocumentListResponse,
)
async def list_documents(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> DocumentListResponse:
    documents, next_cursor = await service.list_documents(
        project_id, limit=limit, cursor=cursor
    )
    return DocumentListResponse(documents=documents, next_cursor=next_cursor)


@router.get(
    "/api/v1/projects/{project_id}/requirements/documents/{slug}",
    response_model=DocumentPublic,
)
async def get_document(
    project_id: str,
    slug: str,
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> DocumentPublic:
    return await service.get_document(project_id, slug)


@router.post(
    "/api/v1/projects/{project_id}/requirements/documents",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    project_id: str,
    payload: DocumentCreate,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> DocumentPublic:
    _require_role(x_user_roles)
    try:
        return await service.create_document(project_id, actor, payload)
    except ConsistencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@router.put(
    "/api/v1/projects/{project_id}/requirements/documents/{slug}",
    response_model=DocumentPublic,
)
async def replace_document(
    project_id: str,
    slug: str,
    payload: DocumentReplace,
    actor: str = Depends(_require_actor),
    if_match: str | None = Header(default=None, alias="If-Match"),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> DocumentPublic:
    _require_role(x_user_roles)
    try:
        return await service.replace_document(
            project_id, slug, actor, payload, if_match
        )
    except ConsistencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@router.delete(
    "/api/v1/projects/{project_id}/requirements/documents/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    project_id: str,
    slug: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> None:
    # Document deletion requires project_owner per R-300-027
    _require_role(x_user_roles, required=("project_owner", "admin"))
    await service.delete_document(project_id, slug, actor)


# ---------------------------------------------------------------------------
# Entity endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/requirements/entities",
    response_model=EntityListResponse,
)
async def list_entities(
    project_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    status_filter: RequirementStatus | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    q: str | None = Query(default=None),
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> EntityListResponse:
    entities, next_cursor = await service.list_entities(
        project_id,
        limit=limit,
        cursor=cursor,
        status_filter=status_filter,
        category_filter=category,
        domain_filter=domain,
        text_filter=q,
    )
    return EntityListResponse(entities=entities, next_cursor=next_cursor)


@router.get(
    "/api/v1/projects/{project_id}/requirements/entities/{entity_id}",
    response_model=EntityPublic,
)
async def get_entity(
    project_id: str,
    entity_id: str,
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> EntityPublic:
    return await service.get_entity(project_id, entity_id)


@router.patch(
    "/api/v1/projects/{project_id}/requirements/entities/{entity_id}",
    response_model=EntityPublic,
)
async def update_entity(
    project_id: str,
    entity_id: str,
    payload: EntityUpdate,
    actor: str = Depends(_require_actor),
    if_match: str | None = Header(default=None, alias="If-Match"),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> EntityPublic:
    _require_role(x_user_roles)
    try:
        return await service.update_entity(
            project_id, entity_id, actor, payload, if_match
        )
    except ConsistencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


@router.delete(
    "/api/v1/projects/{project_id}/requirements/entities/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_entity(
    project_id: str,
    entity_id: str,
    supersedes: str | None = Query(default=None),
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> None:
    _require_role(x_user_roles)
    await service.delete_entity(project_id, entity_id, actor, supersedes=supersedes)


# ---------------------------------------------------------------------------
# History & versions
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/requirements/entities/{entity_id}/history",
    response_model=HistoryListResponse,
)
async def get_history(
    project_id: str,
    entity_id: str,
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> HistoryListResponse:
    return HistoryListResponse(history=await service.list_history(project_id, entity_id))


@router.get(
    "/api/v1/projects/{project_id}/requirements/entities/{entity_id}/versions/{version}",
    response_model=EntityPublic,
)
async def get_entity_version(
    project_id: str,
    entity_id: str,
    version: int,
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> EntityPublic:
    # V1 scope: reconstructing a prior version from history requires reading
    # the MinIO snapshot. Deferred to the reindex/point-in-time work packet.
    _ = service
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            f"point-in-time entity read (v{version} of {entity_id}) deferred to "
            "C5 v2; see R-300-085 roadmap"
        ),
    )


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/requirements/relations",
    response_model=RelationListResponse,
)
async def list_relations(
    project_id: str,
    source: str = Query(..., description="entity id"),
    rel_type: RelationType | None = Query(default=None, alias="type"),
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> RelationListResponse:
    edges = await service.list_relations(project_id, source, rel_type)
    return RelationListResponse(relations=edges)


# ---------------------------------------------------------------------------
# Tailoring audit
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/requirements/tailorings",
    response_model=list[TailoringReport],
)
async def list_tailorings(
    project_id: str,
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> list[TailoringReport]:
    return await service.list_tailorings(project_id)


# ---------------------------------------------------------------------------
# Reindex (R-300-070..073) — operational in v1.5
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/projects/{project_id}/requirements/reindex",
    response_model=ReindexJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex(
    project_id: str,
    _actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> ReindexJob:
    """Trigger an asynchronous rebuild of the derived index. R-300-073:
    admin / project_owner only.

    Idempotent per R-300-072: an in-flight job is returned rather than
    starting a second one.
    """
    _require_role(x_user_roles, required=("admin", "project_owner"))
    return await service.start_reindex(project_id)


@router.get(
    "/api/v1/projects/{project_id}/requirements/reindex/{job_id}",
    response_model=ReindexJob,
)
async def reindex_status(
    project_id: str,
    job_id: str,
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> ReindexJob:
    job = await service.get_reindex_job(job_id)
    if job.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="reindex job not found for this project",
        )
    return job


# ---------------------------------------------------------------------------
# Reconciliation (R-300-063) — operational in v1.5, manual trigger
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/projects/{project_id}/requirements/reconcile",
    response_model=ReconcileReport,
)
async def reconcile(
    project_id: str,
    _actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> ReconcileReport:
    """Single-pass reconciliation between MinIO and ArangoDB. Admin only.

    Not scheduled automatically in v1.5 — operators invoke this manually
    or via a K8s CronJob. The response surfaces repair counts; emit a
    metric/alert if `missing_in_index + stale_in_index > 0` repeatedly.
    """
    _require_role(x_user_roles, required=("admin", "project_owner"))
    return await service.reconcile_tick(project_id)


# ---------------------------------------------------------------------------
# Export (R-300-084 Markdown, streamed per R-300-086) — operational v1.5
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/projects/{project_id}/requirements/export",
    response_model=None,
)
async def export_corpus(
    project_id: str,
    export_format: str = Query(default="md", alias="format"),
    at: str | None = Query(default=None),
    _actor: str = Depends(_require_actor),
    service: RequirementsService = Depends(get_service),
) -> StreamingResponse:
    """Stream the corpus as concatenated Markdown documents.

    `format=md` is supported; ReqIF export (R-300-080) remains deferred.
    `at=<ISO-8601>` point-in-time export (R-300-085) remains deferred.
    """
    if export_format != "md":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"export format {export_format!r} not supported in v1.5 (md only)",
        )
    if at is not None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="point-in-time export (R-300-085) deferred to v2",
        )
    stream = service.export_markdown_stream(project_id)
    return StreamingResponse(stream, media_type="text/markdown")


# ---------------------------------------------------------------------------
# Import — R-300-080 (md format, v1 scope). ReqIF still deferred.
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/projects/{project_id}/requirements/import",
    response_model=ImportReport,
    status_code=status.HTTP_201_CREATED,
)
async def import_corpus(
    project_id: str,
    payload: ImportRequest,
    format: str = Query(default="md"),
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: RequirementsService = Depends(get_service),
) -> ImportReport:
    # RBAC: writing to the corpus requires project_editor or project_owner.
    _require_role(x_user_roles, required=("project_editor", "project_owner", "admin"))
    if format != "md":
        # ReqIF support is tracked as a v2 work item per R-300-080.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                f"format={format!r} not implemented in v1 "
                "(only 'md' is supported; ReqIF deferred)"
            ),
        )
    return await service.import_corpus(project_id, payload, actor)
