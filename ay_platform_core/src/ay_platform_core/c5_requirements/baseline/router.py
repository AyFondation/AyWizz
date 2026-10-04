# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/baseline/router.py
# Description: REST surface for baselines and rendering — 310-SPEC §4.11.
#
#              `readiness` SITS ON ITS OWN FIRST-LEVEL SEGMENT, not under
#              `/baselines/readiness`. The latter would be a literal shadowed
#              by `/baselines/{tag}`, which is the increment-5 bug exactly —
#              and `tests/coherence/test_route_shadowing.py` would now refuse
#              it. Applying the lesson before the check has to is the point
#              of having learned it.
#
#              TAKING A BASELINE IS `project_owner`. It is the act that
#              freezes a record an audit will read, and the gate of
#              `R-310-201` is what gives the act meaning; an editor able to
#              baseline could freeze a corpus whose change tickets they do
#              not own.
#
#              409 CARRIES THE WHOLE READINESS VERDICT, not a sentence. The
#              caller needs every outstanding precondition — same reasoning
#              as the change-closure gate.
#
#              A RENDERING IS RETURNED AS BYTES with a filename, and is
#              CACHED but never authoritative: a second request serves the
#              stored file, and `?refresh=true` re-renders. The manifest is
#              the record; a DOCX is a projection of it.
#
# @relation implements:R-310-200
# @relation implements:R-310-201
# @relation implements:R-310-207
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import ValidationError

from .models import (
    BaselineListResponse,
    BaselineManifest,
    BaselineReadiness,
    BaselineSummary,
    CreateBaselineRequest,
    RenderFormat,
)
from .render import RenderError, render
from .service import (
    BaselineNotFoundError,
    BaselineRefusedError,
    BaselineService,
    ManifestIntegrityError,
    NoPublishedCycleError,
)
from .storage import BaselineExistsError, BaselinePathError, BaselineStorage

router = APIRouter(tags=["baseline"])

_PROJECT = "/api/v1/projects/{project_id}"
_BASELINES = _PROJECT + "/baselines"
_BASELINE = _BASELINES + "/{tag}"

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})

_OWNERS = ("project_owner",)

_FILENAME_EXT = {RenderFormat.DOCX: "docx", RenderFormat.PDF: "pdf"}
_MEDIA = {
    RenderFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    RenderFormat.PDF: "application/pdf",
}


def get_baseline_service(request: Request) -> BaselineService:
    """FastAPI dependency resolved from app.state.baseline_service."""
    service = getattr(request.app.state, "baseline_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="baseline service not configured",
        )
    return service  # type: ignore[no-any-return]


def get_baseline_storage(request: Request) -> BaselineStorage:
    """FastAPI dependency resolved from app.state.baseline_storage.

    Separate from the service because a rendering is cached in storage and
    is explicitly NOT part of the service's record-keeping contract.
    """
    storage = getattr(request.app.state, "baseline_storage", None)
    if storage is None:  # pragma: no cover - wiring error
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="baseline storage not configured",
        )
    return storage  # type: ignore[no-any-return]


def _require_tenant(x_tenant_id: str | None = Header(default=None)) -> str:
    """Resolve the tenant a baseline is taken in.

    Required because a cycle is published at tenant level and tailored per
    project (`R-310-046`): without the tenant there is no cycle to resolve
    and therefore no containers to photograph.
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-Id header missing (forward-auth not applied)",
        )
    return x_tenant_id


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


def _require_role(x_user_roles: str | None, required: tuple[str, ...]) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    roles -= _CONTENT_BLIND_GLOBAL_ROLES
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


def _missing(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


# ---------------------------------------------------------------------------
# The gate — R-310-201
# ---------------------------------------------------------------------------


@router.get(_PROJECT + "/baseline-readiness", response_model=BaselineReadiness)
async def baseline_readiness(
    project_id: str,
    actor: str = Depends(_require_actor),
    service: BaselineService = Depends(get_baseline_service),
) -> BaselineReadiness:
    """Evaluate `R-310-201` without taking a baseline.

    Its own first-level segment so no `/{tag}` route can shadow it — see
    the module header.
    """
    try:
        return await service.readiness(project_id)
    except BaselinePathError as exc:
        raise _unprocessable(exc) from exc


# ---------------------------------------------------------------------------
# Taking and reading a baseline — R-310-200
# ---------------------------------------------------------------------------


@router.post(
    _BASELINE, response_model=BaselineManifest, status_code=status.HTTP_201_CREATED
)
async def create_baseline(
    project_id: str,
    tag: str,
    body: CreateBaselineRequest,
    tenant_id: str = Depends(_require_tenant),
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: BaselineService = Depends(get_baseline_service),
) -> BaselineManifest:
    """Take a baseline, if and only if `R-310-201` permits it.

    409 on refusal, carrying the full readiness verdict; 409 too on a tag
    already taken, because a baseline is immutable (`R-310-204`).
    """
    _require_role(x_user_roles, _OWNERS)
    try:
        return await service.create(
            tenant_id,
            project_id,
            tag,
            cycle_id=body.cycle_id,
            actor=actor,
            note=body.note,
        )
    except BaselineRefusedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.readiness.model_dump(mode="json"),
        ) from exc
    except BaselineExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except NoPublishedCycleError as exc:
        # 409, not 404: the project exists and the caller is entitled; the
        # PROCESS is not in a state where a baseline means anything.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except BaselinePathError as exc:
        raise _unprocessable(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc


@router.get(_BASELINES, response_model=BaselineListResponse)
async def list_baselines(
    project_id: str,
    actor: str = Depends(_require_actor),
    service: BaselineService = Depends(get_baseline_service),
) -> BaselineListResponse:
    """List a project's baselines, with their counts but not their entries."""
    try:
        tags = await service.list_tags(project_id)
    except BaselinePathError as exc:
        raise _unprocessable(exc) from exc
    summaries: list[BaselineSummary] = []
    for tag in tags:
        manifest = await service.get(project_id, tag)
        summaries.append(
            BaselineSummary(
                tag=manifest.tag,
                project_id=manifest.project_id,
                cycle_id=manifest.cycle_id,
                cycle_version=manifest.cycle_version,
                created_by=manifest.created_by,
                created_at=manifest.created_at,
                object_count=manifest.object_count,
                link_count=manifest.link_count,
                note=manifest.note,
            )
        )
    return BaselineListResponse(
        baselines=tuple(summaries), count=len(summaries)
    )


@router.get(_BASELINE, response_model=BaselineManifest)
async def get_baseline(
    project_id: str,
    tag: str,
    actor: str = Depends(_require_actor),
    service: BaselineService = Depends(get_baseline_service),
) -> BaselineManifest:
    """Return one manifest — the record itself."""
    try:
        return await service.get(project_id, tag)
    except BaselineNotFoundError as exc:
        raise _missing(exc) from exc
    except BaselinePathError as exc:
        raise _unprocessable(exc) from exc


# ---------------------------------------------------------------------------
# Rendering — R-310-207
# ---------------------------------------------------------------------------


@router.get(_BASELINE + "/render/{fmt}")
async def render_baseline(
    project_id: str,
    tag: str,
    fmt: RenderFormat,
    refresh: bool = False,
    actor: str = Depends(_require_actor),
    service: BaselineService = Depends(get_baseline_service),
    storage: BaselineStorage = Depends(get_baseline_storage),
) -> Response:
    """Render a baseline to DOCX or PDF (`R-310-207`).

    Serves the cached rendering unless `refresh=true`. The cache is a
    convenience over a projection; the manifest is the record, so a stale
    rendering is never a correctness problem — but `refresh` exists because
    a template change should be visible without taking a new baseline.

    422 when the manifest no longer matches what it names: the content hash
    is in the manifest precisely so that failure is loud (`R-310-204`).
    """
    if not refresh:
        cached = await storage.get_rendering(project_id, tag, fmt)
        if cached is not None:
            return _document_response(cached, tag, fmt)

    try:
        manifest = await service.get(project_id, tag)
        entries = await service.resolve(project_id, tag)
    except BaselineNotFoundError as exc:
        raise _missing(exc) from exc
    except ManifestIntegrityError as exc:
        raise _unprocessable(exc) from exc
    except BaselinePathError as exc:
        raise _unprocessable(exc) from exc

    try:
        rendering = render(manifest, entries, fmt)
    except RenderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    await storage.put_rendering(project_id, tag, fmt, rendering.payload)
    return _document_response(rendering.payload, tag, fmt)


def _document_response(payload: bytes, tag: str, fmt: RenderFormat) -> Response:
    """Return a downloadable document response."""
    return Response(
        content=payload,
        media_type=_MEDIA[fmt],
        headers={
            "Content-Disposition": (
                f'attachment; filename="{tag}.{_FILENAME_EXT[fmt]}"'
            )
        },
    )
