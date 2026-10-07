# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/objects/router.py
# Description: FastAPI routes for the object-grain document model
#              (310-SPEC-DOC-TRACEABILITY §4.1, §4.7, §4.10).
#
#              Role gates follow E-100-002: reads are merely authenticated,
#              writes require project_editor or project_owner, and breaking a
#              foreign lease is reserved to project_owner (R-310-193 — the
#              container owner). `platform_manager` is excluded from every
#              route here: these are content endpoints.
#
#              Error mapping is deliberate rather than incidental:
#                - a stale `expected_version` is 409, not 400 — the caller's
#                  request was well-formed, the world moved (R-310-192);
#                - a foreign lease is 423 Locked, not 403 — it is a temporary
#                  state naming a holder, not an authorisation verdict.
#
# @relation implements:R-310-124
# @relation implements:R-310-190
# @relation implements:R-310-192
# @relation implements:R-310-193
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ay_platform_core.c2_auth.forward_auth import (
    require_project_content_role,
)

from .locks import LockHeldError, LockNotHeldError
from .models import (
    DocObject,
    DocObjectPublic,
    ObjectCreate,
    ObjectListResponse,
    ObjectLock,
    ObjectLockRequest,
    ObjectReviewRequest,
    ObjectVersionListResponse,
    WorkingDraft,
    WorkingDraftWrite,
)
from .service import (
    NoDraftError,
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectService,
)
from .storage import ObjectPathError

# Defence in depth (E-100-002 v8). Every route below whose path carries a
# `/projects/<id>/` segment requires a project grant on THAT project, in
# addition to whatever stricter gate the individual route declares. The
# gateway already refuses such a request, so this is the SECOND line: a
# component reached directly — a port-forward, a mesh topology, a
# mistaken `expose:` — meets no gate at all without it. Attached to the
# ROUTER so a route nobody has written yet inherits it; the dependency
# self-limits to project paths, leaving tenant-level and /health routes
# in mixed routers untouched. See `c2_auth.forward_auth`.
router = APIRouter(
    tags=["objects"],
    dependencies=[Depends(require_project_content_role)],
)

_BASE = "/api/v1/projects/{project_id}/containers/{container}/objects"

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})

_EDITORS = ("project_editor", "project_owner")
_OWNERS = ("project_owner",)


def get_object_service(request: Request) -> ObjectService:
    """FastAPI dependency resolved from app.state.object_service."""
    service = getattr(request.app.state, "object_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="object service not configured",
        )
    return service  # type: ignore[no-any-return]


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


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _conflict(exc: ObjectConflictError) -> HTTPException:
    # 409, not 400: the request was well-formed; the object moved under it.
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": str(exc),
            "expected_version": exc.expected,
            "actual_version": exc.actual,
        },
    )


def _locked(exc: LockHeldError) -> HTTPException:
    # 423, not 403: a lease is a temporary state naming a holder, not an
    # authorisation verdict. The caller may legitimately retry later, and the
    # UI needs the holder to say who to ask (R-310-193).
    return HTTPException(
        status_code=status.HTTP_423_LOCKED,
        detail={
            "message": str(exc),
            "holder": exc.lock.holder,
            "holder_kind": exc.lock.holder_kind.value,
            "expires_at": exc.lock.expires_at.isoformat(),
        },
    )


def _bad_path(exc: ObjectPathError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(_BASE, response_model=ObjectListResponse)
async def list_objects(
    project_id: str,
    container: str,
    _actor: str = Depends(_require_actor),
    service: ObjectService = Depends(get_object_service),
) -> ObjectListResponse:
    try:
        objects = await service.list_container(project_id, container)
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc
    return ObjectListResponse(objects=objects)


@router.get(f"{_BASE}/{{object_id}}", response_model=DocObjectPublic)
async def get_object(
    project_id: str,
    container: str,
    object_id: str,
    _actor: str = Depends(_require_actor),
    service: ObjectService = Depends(get_object_service),
) -> DocObjectPublic:
    try:
        return await service.get(project_id, container, object_id)
    except ObjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc


@router.get(
    f"{_BASE}/{{object_id}}/versions", response_model=ObjectVersionListResponse
)
async def list_object_versions(
    project_id: str,
    container: str,
    object_id: str,
    _actor: str = Depends(_require_actor),
    service: ObjectService = Depends(get_object_service),
) -> ObjectVersionListResponse:
    try:
        versions = await service.list_versions(project_id, container, object_id)
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc
    return ObjectVersionListResponse(object_id=object_id, versions=versions)


@router.get(
    f"{_BASE}/{{object_id}}/versions/{{version}}", response_model=DocObject
)
async def get_object_version(
    project_id: str,
    container: str,
    object_id: str,
    version: int,
    _actor: str = Depends(_require_actor),
    service: ObjectService = Depends(get_object_service),
) -> DocObject:
    try:
        return await service.get_version(project_id, container, object_id, version)
    except ObjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc


@router.get(f"{_BASE}/{{object_id}}/draft", response_model=WorkingDraft | None)
async def get_object_draft(
    project_id: str,
    container: str,
    object_id: str,
    _actor: str = Depends(_require_actor),
    service: ObjectService = Depends(get_object_service),
) -> WorkingDraft | None:
    try:
        return await service.get_draft(project_id, container, object_id)
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post(
    _BASE, response_model=DocObject, status_code=status.HTTP_201_CREATED
)
async def create_object(
    project_id: str,
    container: str,
    payload: ObjectCreate,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ObjectService = Depends(get_object_service),
) -> DocObject:
    _require_role(x_user_roles, _EDITORS)
    try:
        return await service.create(
            project_id,
            container,
            object_id=payload.object_id,
            object_type=payload.type,
            actor=actor,
            ordinal=payload.ordinal,
            body=payload.body,
            notation=payload.notation,
            parent=payload.parent,
            produced_by=payload.produced_by,
            cycle_version=payload.cycle_version,
        )
    except ObjectConflictError as exc:
        raise _conflict(exc) from exc
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc


@router.put(f"{_BASE}/{{object_id}}/draft", response_model=WorkingDraft)
async def write_object_draft(
    project_id: str,
    container: str,
    object_id: str,
    payload: WorkingDraftWrite,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ObjectService = Depends(get_object_service),
) -> WorkingDraft:
    _require_role(x_user_roles, _EDITORS)
    try:
        return await service.write_draft(
            project_id, container, object_id, payload, actor=actor
        )
    except ObjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ObjectConflictError as exc:
        raise _conflict(exc) from exc
    except LockHeldError as exc:
        raise _locked(exc) from exc
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc


@router.post(f"{_BASE}/{{object_id}}/review", response_model=DocObject)
async def review_object(
    project_id: str,
    container: str,
    object_id: str,
    payload: ObjectReviewRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ObjectService = Depends(get_object_service),
) -> DocObject:
    _require_role(x_user_roles, _EDITORS)
    try:
        return await service.review(
            project_id, container, object_id, payload, actor=actor
        )
    except ObjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ObjectConflictError as exc:
        raise _conflict(exc) from exc
    except NoDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except LockHeldError as exc:
        raise _locked(exc) from exc
    except ObjectPathError as exc:
        raise _bad_path(exc) from exc


# ---------------------------------------------------------------------------
# Locking — R-310-190 / R-310-193
# ---------------------------------------------------------------------------


@router.post(f"{_BASE}/{{object_id}}/lock", response_model=ObjectLock)
async def acquire_object_lock(
    project_id: str,
    container: str,
    object_id: str,
    payload: ObjectLockRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ObjectService = Depends(get_object_service),
) -> ObjectLock:
    _require_role(x_user_roles, _EDITORS)
    try:
        return await service.acquire_lock(
            project_id, object_id, actor, payload.holder_kind
        )
    except LockHeldError as exc:
        raise _locked(exc) from exc


@router.delete(
    f"{_BASE}/{{object_id}}/lock", status_code=status.HTTP_204_NO_CONTENT
)
async def release_object_lock(
    project_id: str,
    container: str,
    object_id: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ObjectService = Depends(get_object_service),
) -> None:
    _require_role(x_user_roles, _EDITORS)
    try:
        await service.release_lock(project_id, object_id, actor)
    except LockNotHeldError as exc:
        # 409, not 403: the caller is entitled to release locks, just not
        # this one — the object is held by somebody else.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.delete(
    f"{_BASE}/{{object_id}}/lock/force", status_code=status.HTTP_204_NO_CONTENT
)
async def force_release_object_lock(
    project_id: str,
    container: str,
    object_id: str,
    _actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: ObjectService = Depends(get_object_service),
) -> None:
    # R-310-193 reserves breaking a foreign lease to the container owner.
    _require_role(x_user_roles, _OWNERS)
    await service.force_release_lock(project_id, object_id)
