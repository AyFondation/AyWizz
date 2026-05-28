# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/router.py
# Description: FastAPI APIRouter for C7 per 400-SPEC §6.1. Identity comes
#              from Traefik forward-auth headers (X-User-Id, X-User-Roles,
#              X-Tenant-Id), propagated by C1 / C2 as on the other
#              components.
#
# @relation implements:R-400-040
# @relation implements:R-400-070
# @relation implements:E-400-005
# C7 also realises the C7 side of the C12 → C7 ingestion contract:
# @relation implements:R-100-080 R-100-081
# =============================================================================

from __future__ import annotations

from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Response,
    status,
)

from ay_platform_core.c7_memory.kg.ontology import StructuralKGResult
from ay_platform_core.c7_memory.models import (
    ChunkIngestRequest,
    ChunkPublic,
    EntityEmbedRequest,
    KGExtractionResult,
    KGSummary,
    QuotaStatus,
    RetrievalRequest,
    RetrievalResponse,
    SourceIngestRequest,
    SourceListResponse,
    SourcePublic,
)
from ay_platform_core.c7_memory.service import MemoryService, get_service

router = APIRouter(tags=["memory"])

# ---------------------------------------------------------------------------
# RBAC helpers — identical pattern to C3/C4/C5
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


def _require_role(
    x_user_roles: str | None,
    required: tuple[str, ...],
) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/memory/retrieve",
    response_model=RetrievalResponse,
)
async def retrieve(
    payload: RetrievalRequest,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: MemoryService = Depends(get_service),
) -> RetrievalResponse:
    return await service.retrieve(payload, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Sources (admin/operator path — production upload goes via C12)
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/memory/projects/{project_id}/sources",
    response_model=SourcePublic,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_source(
    project_id: str,
    payload: SourceIngestRequest,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: MemoryService = Depends(get_service),
) -> SourcePublic:
    # v1 admin-only direct ingest — production upload path is C12.
    _require_role(x_user_roles, required=("project_editor", "project_owner", "admin"))
    if payload.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload.project_id does not match URL project_id",
        )
    return await service.ingest_source(payload, tenant_id=tenant_id)


# D-020 session 7 — `POST /sources/upload` (multipart) removed. The platform
# ingestion path is now `C12 (n8n) → C13 (AyExtractor) → C7 /ingest-chunks`
# per R-100-081 v2 ; the in-process parse + chunk pipeline was retired
# alongside `MemoryService.ingest_uploaded_source`. Operators uploading
# files SHALL use the n8n webhook `POST /uploads/extract-and-ingest`
# (see `infra/c12_workflow/workflows/extract_and_ingest.json`).


@router.post(
    "/api/v1/memory/projects/{project_id}/sources/{source_id}/ingest-chunks",
    response_model=SourcePublic,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_chunks(
    project_id: str,
    source_id: str,
    payload: ChunkIngestRequest,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: MemoryService = Depends(get_service),
) -> SourcePublic:
    """R-400-223 v2 — pure-INSERT path for C13-produced chunks (D-020).

    Accepts a `ChunkIngestRequest` carrying R-400-222 v2 `ChunkRich`
    items + the embedding model metadata stamped by C13 (D-020 v2 §B1).
    C7 takes the embedding vectors AS-IS and persists them into Arango
    without invoking its own embedder — keeping the reproducible-rebuild
    invariant of R-400-207 (every embedding is replayable from the MinIO
    artifact set produced by C13).

    Role gate identical to `POST /sources`: `project_editor` /
    `project_owner` / `admin`. `tenant_manager` excluded by E-100-002 v2.

    Backward-compat: when a chunk's `embedding` field is None, C7 falls
    back to its own embedder (transitional, removed in D-020 session 7).

    @relation implements:R-400-223 R-100-081 R-400-220
    """
    _require_role(x_user_roles, required=("project_editor", "project_owner", "admin"))
    return await service.ingest_chunks_from_extractor(
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=source_id,
        payload=payload,
    )


@router.post(
    "/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-kg",
    response_model=KGExtractionResult,
    status_code=status.HTTP_200_OK,
)
async def extract_kg(
    project_id: str,
    source_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: MemoryService = Depends(get_service),
) -> KGExtractionResult:
    """Phase F.1 — extract entities + relations from an existing source
    via the C8 LLM gateway. Same role gate as `/sources` ingest:
    `project_editor` / `project_owner` / `admin`. `tenant_manager`
    excluded by E-100-002 v2."""
    _require_role(x_user_roles, required=("project_editor", "project_owner", "admin"))
    return await service.extract_kg(
        tenant_id=tenant_id, project_id=project_id, source_id=source_id,
    )


@router.post(
    "/api/v1/memory/projects/{project_id}/sources/{source_id}/extract-structural",
    response_model=StructuralKGResult,
    status_code=status.HTTP_200_OK,
)
async def extract_structural_kg(
    project_id: str,
    source_id: str,
    kind: Literal["requirements", "code"] = "requirements",
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: MemoryService = Depends(get_service),
) -> StructuralKGResult:
    """V2 #3-A.a (R-400-200) — deterministic schema-guided L1 extraction over
    an existing source. `kind=requirements` (default) parses spec entity
    blocks ; `kind=code` parses the Python AST (reads raw bytes). Same role
    gate as `extract-kg`: `project_editor` / `project_owner` / `admin` ;
    `tenant_manager` excluded by E-100-002 v2."""
    _require_role(x_user_roles, required=("project_editor", "project_owner", "admin"))
    return await service.extract_structural_kg(
        tenant_id=tenant_id, project_id=project_id, source_id=source_id, kind=kind,
    )


@router.get(
    "/api/v1/memory/projects/{project_id}/sources",
    response_model=SourceListResponse,
)
async def list_sources(
    project_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: MemoryService = Depends(get_service),
) -> SourceListResponse:
    return await service.list_sources(tenant_id, project_id)


@router.get(
    "/api/v1/memory/projects/{project_id}/sources/{source_id}",
    response_model=SourcePublic,
)
async def get_source(
    project_id: str,
    source_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: MemoryService = Depends(get_service),
) -> SourcePublic:
    return await service.get_source(tenant_id, project_id, source_id)


@router.get(
    "/api/v1/memory/projects/{project_id}/kg/summary",
    response_model=KGSummary,
)
async def kg_summary(
    project_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: MemoryService = Depends(get_service),
) -> KGSummary:
    """Simple graph bootstrap (R-400-200/201): entity/relation counts +
    a sample of triples with provenance for the project's KG."""
    return await service.kg_summary(tenant_id, project_id)


@router.get(
    "/api/v1/memory/projects/{project_id}/sources/{source_id}/blob",
    responses={
        200: {"description": "Raw source bytes streamed back to the caller."},
        404: {"description": "Source row exists but its blob is missing."},
        503: {"description": "Blob storage not configured (no MinIO wired)."},
    },
)
async def download_source_blob(
    project_id: str,
    source_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: MemoryService = Depends(get_service),
) -> Response:
    """Stream the raw uploaded file from MinIO. Same project-scope auth
    as `GET /sources/{source_id}` (its metadata sibling). The
    `Content-Disposition` header carries a synthesised filename so
    browsers can download with a sensible name.

    v1: full bytes loaded into memory then returned (capped at
    `C7_MAX_UPLOAD_BYTES`, default 50 MiB). True streaming chunks
    deferred until uploads exceed that budget.
    """
    blob, mime_type, filename = await service.download_source(
        tenant_id, project_id, source_id,
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return Response(content=blob, media_type=mime_type, headers=headers)


@router.delete(
    "/api/v1/memory/projects/{project_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_source(
    project_id: str,
    source_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: MemoryService = Depends(get_service),
) -> None:
    # R-400-070: source deletion requires project_owner or admin.
    _require_role(x_user_roles, required=("project_owner", "admin"))
    await service.delete_source(tenant_id, project_id, source_id)


# D-020 session 7 — `POST /sources/{id}/reprocess` removed. In the new
# pipeline, "reprocess" means re-triggering the C12 n8n workflow against
# the same source_id (which produces a fresh C13 run + a pure-INSERT
# call into C7 /ingest-chunks). The per-source reprocess responsibility
# moved out of C7 ; R-400-208 staleness detection (via
# `processing_version` stamped at ingest time) is preserved on the
# surviving GET /sources/{id} surface.


# ---------------------------------------------------------------------------
# Entity embedding — normally event-driven; exposed as admin endpoint for
# tests and manual re-embed operations.
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/memory/entities/embed",
    response_model=ChunkPublic,
    status_code=status.HTTP_201_CREATED,
)
async def embed_entity(
    payload: EntityEmbedRequest,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: MemoryService = Depends(get_service),
) -> ChunkPublic:
    _require_role(x_user_roles, required=("admin",))
    return await service.embed_entity(payload, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/memory/projects/{project_id}/quota",
    response_model=QuotaStatus,
)
async def quota(
    project_id: str,
    _user: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: MemoryService = Depends(get_service),
) -> QuotaStatus:
    return await service.quota(tenant_id, project_id)


# ---------------------------------------------------------------------------
# Refresh — deferred to a follow-up (R-400-060/061): stub 501.
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/memory/projects/{project_id}/refresh",
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def refresh(
    project_id: str,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
) -> None:
    _ = project_id
    _require_role(x_user_roles, required=("admin",))
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="memory refresh deferred to a follow-up (R-400-060/061)",
    )


@router.get(
    "/api/v1/memory/refresh/{job_id}",
    response_model=None,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def refresh_status(
    job_id: str,
    _user: str = Depends(_require_actor),
) -> None:
    _ = job_id
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="memory refresh deferred to a follow-up (R-400-060/061)",
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/memory/health",
    response_model=None,
)
async def health(
    service: MemoryService = Depends(get_service),
) -> dict[str, str]:
    # Minimal liveness — the service dependency will 503 if not initialised.
    _ = service
    return {"status": "ok"}
