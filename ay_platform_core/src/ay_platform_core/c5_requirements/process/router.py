# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/process/router.py
# Description: Authoring surface for cycles and workflows (R-310-047).
#
#              Two scopes, two gates, ratified as C-02 in the dev plan:
#                - TENANT scope holds the standard catalogue and is written
#                  by `tenant_admin`;
#                - PROJECT scope holds tailorings and is written by
#                  `project_owner` (R-310-022, R-310-046).
#              No new role is introduced: a method-engineer role would touch
#              E-100-002, the §13 route catalogue and the isolation matrix
#              for no behaviour the two existing roles cannot express.
#
#              Reads are merely authenticated: everyone working on a project
#              needs to see the process they are working under, and hiding it
#              would make the allocation citations of R-310-066 unverifiable.
#
#              `.../resolved` is the endpoint every consumer actually wants:
#              "what applies to THIS project?" — the project's published
#              tailoring when it has one, the tenant catalogue otherwise.
#
# @relation implements:R-310-022
# @relation implements:R-310-023
# @relation implements:R-310-046
# @relation implements:R-310-047
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from ay_platform_core.c2_auth.forward_auth import (
    require_project_content_role,
)

from .models import (
    ActivityBinding,
    CycleDraftCreate,
    CycleDraftUpdate,
    CyclePublic,
    ProcessVersionListResponse,
    ProcessVersionRow,
    WorkflowDraftCreate,
    WorkflowDraftUpdate,
    WorkflowPublic,
)
from .service import (
    ActivityNotPermittedError,
    NotADraftError,
    ProcessNotFoundError,
    ProcessService,
)
from .storage import AlreadyPublishedError, ProcessPathError

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
    tags=["process"],
    dependencies=[Depends(require_project_content_role)],
)

_TENANT = "/api/v1/process"
_PROJECT = "/api/v1/projects/{project_id}/process"

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin"})

_CATALOGUE_WRITERS = ("tenant_admin",)
_TAILORING_WRITERS = ("project_owner",)


def get_process_service(request: Request) -> ProcessService:
    """FastAPI dependency resolved from app.state.process_service."""
    service = getattr(request.app.state, "process_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="process service not configured",
        )
    return service  # type: ignore[no-any-return]


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
            detail="X-Tenant-Id header missing (forward-auth not applied)",
        )
    return x_tenant_id


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


def _conflict(exc: Exception) -> HTTPException:
    # 409, not 400: the body was well-formed and the caller is entitled to
    # write; the version it names is simply no longer a draft (R-310-023).
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _unprocessable(exc: ValidationError) -> HTTPException:
    # A draft that cannot be approved as it stands — most often a workflow
    # with no checks (R-310-041). 422 because the stored entity, not the
    # request, is what fails validation.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


def _rows(raw: list[dict[str, object]]) -> ProcessVersionListResponse:
    return ProcessVersionListResponse(
        versions=[ProcessVersionRow.model_validate(r) for r in raw]
    )


# ---------------------------------------------------------------------------
# Tenant catalogue — cycles
# ---------------------------------------------------------------------------


@router.get(f"{_TENANT}/cycles", response_model=ProcessVersionListResponse)
async def list_tenant_cycles(
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> ProcessVersionListResponse:
    return _rows(await service.list_cycle_versions(tenant_id, None))


@router.post(
    f"{_TENANT}/cycles",
    response_model=CyclePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_cycle(
    payload: CycleDraftCreate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    _require_role(x_user_roles, _CATALOGUE_WRITERS)
    return await _create_cycle(service, tenant_id, None, payload, actor)


@router.get(
    f"{_TENANT}/cycles/{{cycle_id}}/versions/{{version}}",
    response_model=CyclePublic,
)
async def get_tenant_cycle(
    cycle_id: str,
    version: int,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    try:
        return CyclePublic.from_definition(
            await service.get_cycle(tenant_id, None, cycle_id, version)
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    f"{_TENANT}/cycles/{{cycle_id}}/versions/{{version}}",
    response_model=CyclePublic,
)
async def update_tenant_cycle(
    cycle_id: str,
    version: int,
    payload: CycleDraftUpdate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    _require_role(x_user_roles, _CATALOGUE_WRITERS)
    return await _update_cycle(service, tenant_id, None, cycle_id, version, payload, actor)


@router.post(
    f"{_TENANT}/cycles/{{cycle_id}}/versions/{{version}}/publish",
    response_model=CyclePublic,
)
async def publish_tenant_cycle(
    cycle_id: str,
    version: int,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    _require_role(x_user_roles, _CATALOGUE_WRITERS)
    return await _publish_cycle(service, tenant_id, None, cycle_id, version, actor)


# ---------------------------------------------------------------------------
# Tenant catalogue — workflows
# ---------------------------------------------------------------------------


@router.get(f"{_TENANT}/workflows", response_model=ProcessVersionListResponse)
async def list_tenant_workflows(
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> ProcessVersionListResponse:
    return _rows(await service.list_workflow_versions(tenant_id, None))


@router.post(
    f"{_TENANT}/workflows",
    response_model=WorkflowPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_workflow(
    payload: WorkflowDraftCreate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    _require_role(x_user_roles, _CATALOGUE_WRITERS)
    return await _create_workflow(service, tenant_id, None, payload, actor)


@router.get(
    f"{_TENANT}/workflows/{{workflow_id}}/versions/{{version}}",
    response_model=WorkflowPublic,
)
async def get_tenant_workflow(
    workflow_id: str,
    version: int,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    try:
        return WorkflowPublic.from_definition(
            await service.get_workflow(tenant_id, None, workflow_id, version)
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    f"{_TENANT}/workflows/{{workflow_id}}/versions/{{version}}",
    response_model=WorkflowPublic,
)
async def update_tenant_workflow(
    workflow_id: str,
    version: int,
    payload: WorkflowDraftUpdate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    _require_role(x_user_roles, _CATALOGUE_WRITERS)
    return await _update_workflow(
        service, tenant_id, None, workflow_id, version, payload, actor
    )


@router.post(
    f"{_TENANT}/workflows/{{workflow_id}}/versions/{{version}}/publish",
    response_model=WorkflowPublic,
)
async def publish_tenant_workflow(
    workflow_id: str,
    version: int,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    _require_role(x_user_roles, _CATALOGUE_WRITERS)
    return await _publish_workflow(
        service, tenant_id, None, workflow_id, version, actor
    )


# ---------------------------------------------------------------------------
# Project tailoring — cycles
# ---------------------------------------------------------------------------


@router.post(
    f"{_PROJECT}/cycles",
    response_model=CyclePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_cycle(
    project_id: str,
    payload: CycleDraftCreate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    _require_role(x_user_roles, _TAILORING_WRITERS)
    return await _create_cycle(service, tenant_id, project_id, payload, actor)


@router.get(
    f"{_PROJECT}/cycles/{{cycle_id}}/versions/{{version}}",
    response_model=CyclePublic,
)
async def get_project_cycle(
    project_id: str,
    cycle_id: str,
    version: int,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    try:
        return CyclePublic.from_definition(
            await service.get_cycle(tenant_id, project_id, cycle_id, version)
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    f"{_PROJECT}/cycles/{{cycle_id}}/versions/{{version}}",
    response_model=CyclePublic,
)
async def update_project_cycle(
    project_id: str,
    cycle_id: str,
    version: int,
    payload: CycleDraftUpdate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    _require_role(x_user_roles, _TAILORING_WRITERS)
    return await _update_cycle(
        service, tenant_id, project_id, cycle_id, version, payload, actor
    )


@router.post(
    f"{_PROJECT}/cycles/{{cycle_id}}/versions/{{version}}/publish",
    response_model=CyclePublic,
)
async def publish_project_cycle(
    project_id: str,
    cycle_id: str,
    version: int,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    _require_role(x_user_roles, _TAILORING_WRITERS)
    return await _publish_cycle(
        service, tenant_id, project_id, cycle_id, version, actor
    )


@router.get(f"{_PROJECT}/cycles/{{cycle_id}}/resolved", response_model=CyclePublic)
async def resolve_project_cycle(
    project_id: str,
    cycle_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> CyclePublic:
    resolved = await service.resolve_cycle(tenant_id, project_id, cycle_id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no published cycle {cycle_id!r} applies to {project_id!r} — "
                "neither a project tailoring nor a tenant catalogue entry"
            ),
        )
    return CyclePublic.from_definition(resolved)


@router.get(
    f"{_PROJECT}/cycles/{{cycle_id}}/containers/{{container}}/activity",
    response_model=ActivityBinding,
)
async def resolve_container_activity(
    project_id: str,
    cycle_id: str,
    container: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> ActivityBinding:
    """Return the activity permitted on a container, or refuse with a reason.

    R-310-025's precondition, exposed so the workbench can tell a user WHY
    work cannot start — a missing cycle, a missing binding, or an
    unpublished workflow — instead of showing an inert button.
    """
    try:
        return await service.resolve_activity(
            tenant_id, project_id, cycle_id, container
        )
    except ActivityNotPermittedError as exc:
        # 409: the request is well-formed and the caller is entitled to ask;
        # the process simply does not permit an activity here yet.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": exc.reason.value, "message": exc.detail},
        ) from exc


# ---------------------------------------------------------------------------
# Project tailoring — workflows
# ---------------------------------------------------------------------------


@router.post(
    f"{_PROJECT}/workflows",
    response_model=WorkflowPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_project_workflow(
    project_id: str,
    payload: WorkflowDraftCreate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    _require_role(x_user_roles, _TAILORING_WRITERS)
    return await _create_workflow(service, tenant_id, project_id, payload, actor)


@router.get(
    f"{_PROJECT}/workflows/{{workflow_id}}/versions/{{version}}",
    response_model=WorkflowPublic,
)
async def get_project_workflow(
    project_id: str,
    workflow_id: str,
    version: int,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    try:
        return WorkflowPublic.from_definition(
            await service.get_workflow(tenant_id, project_id, workflow_id, version)
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    f"{_PROJECT}/workflows/{{workflow_id}}/versions/{{version}}",
    response_model=WorkflowPublic,
)
async def update_project_workflow(
    project_id: str,
    workflow_id: str,
    version: int,
    payload: WorkflowDraftUpdate,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    _require_role(x_user_roles, _TAILORING_WRITERS)
    return await _update_workflow(
        service, tenant_id, project_id, workflow_id, version, payload, actor
    )


@router.post(
    f"{_PROJECT}/workflows/{{workflow_id}}/versions/{{version}}/publish",
    response_model=WorkflowPublic,
)
async def publish_project_workflow(
    project_id: str,
    workflow_id: str,
    version: int,
    actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    x_user_roles: str | None = Header(default=None),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    _require_role(x_user_roles, _TAILORING_WRITERS)
    return await _publish_workflow(
        service, tenant_id, project_id, workflow_id, version, actor
    )


@router.get(
    f"{_PROJECT}/workflows/{{workflow_id}}/resolved", response_model=WorkflowPublic
)
async def resolve_project_workflow(
    project_id: str,
    workflow_id: str,
    _actor: str = Depends(_require_actor),
    tenant_id: str = Depends(_require_tenant),
    service: ProcessService = Depends(get_process_service),
) -> WorkflowPublic:
    resolved = await service.resolve_workflow(tenant_id, project_id, workflow_id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no published workflow {workflow_id!r} applies to "
                f"{project_id!r} — neither a project tailoring nor a tenant "
                "catalogue entry"
            ),
        )
    return WorkflowPublic.from_definition(resolved)


# ---------------------------------------------------------------------------
# Shared handler bodies — one implementation per operation, two scopes
# ---------------------------------------------------------------------------


async def _create_cycle(
    service: ProcessService,
    tenant_id: str,
    project_id: str | None,
    payload: CycleDraftCreate,
    actor: str,
) -> CyclePublic:
    try:
        draft = await service.create_cycle_draft(
            tenant_id, project_id, cycle_id=payload.cycle_id, title=payload.title,
            containers=payload.containers, actor=actor,
            tailoring_of=payload.tailoring_of,
            tailoring_rationale=payload.tailoring_rationale,
        )
    except ValidationError as exc:
        # The model refuses a tailoring with no rationale (R-310-046) and a
        # control flow that loops (R-310-043). Both are caller errors: 422,
        # not the 500 an uncaught ValidationError would produce.
        raise _unprocessable(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CyclePublic.from_definition(draft)


async def _update_cycle(
    service: ProcessService,
    tenant_id: str,
    project_id: str | None,
    cycle_id: str,
    version: int,
    payload: CycleDraftUpdate,
    actor: str,
) -> CyclePublic:
    try:
        updated = await service.update_cycle_draft(
            tenant_id, project_id, cycle_id, version,
            title=payload.title, containers=payload.containers, actor=actor,
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except NotADraftError as exc:
        raise _conflict(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CyclePublic.from_definition(updated)


async def _publish_cycle(
    service: ProcessService,
    tenant_id: str,
    project_id: str | None,
    cycle_id: str,
    version: int,
    actor: str,
) -> CyclePublic:
    try:
        published = await service.publish_cycle(
            tenant_id, project_id, cycle_id, version, actor=actor
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except (NotADraftError, AlreadyPublishedError) as exc:
        raise _conflict(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    return CyclePublic.from_definition(published)


async def _create_workflow(
    service: ProcessService,
    tenant_id: str,
    project_id: str | None,
    payload: WorkflowDraftCreate,
    actor: str,
) -> WorkflowPublic:
    try:
        draft = await service.create_workflow_draft(
            tenant_id, project_id, workflow_id=payload.workflow_id,
            intent=payload.intent, steps=payload.steps, checks=payload.checks,
            inputs=payload.inputs, outputs=payload.outputs,
            constraints=payload.constraints, examples=payload.examples,
            actor=actor,
            tailoring_of=payload.tailoring_of,
            tailoring_rationale=payload.tailoring_rationale,
        )
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkflowPublic.from_definition(draft)


async def _update_workflow(
    service: ProcessService,
    tenant_id: str,
    project_id: str | None,
    workflow_id: str,
    version: int,
    payload: WorkflowDraftUpdate,
    actor: str,
) -> WorkflowPublic:
    try:
        updated = await service.update_workflow_draft(
            tenant_id, project_id, workflow_id, version, actor=actor,
            intent=payload.intent, steps=payload.steps, checks=payload.checks,
            inputs=payload.inputs, outputs=payload.outputs,
            constraints=payload.constraints, examples=payload.examples,
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except NotADraftError as exc:
        raise _conflict(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    except ProcessPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WorkflowPublic.from_definition(updated)


async def _publish_workflow(
    service: ProcessService,
    tenant_id: str,
    project_id: str | None,
    workflow_id: str,
    version: int,
    actor: str,
) -> WorkflowPublic:
    try:
        published = await service.publish_workflow(
            tenant_id, project_id, workflow_id, version, actor=actor
        )
    except ProcessNotFoundError as exc:
        raise _not_found(exc) from exc
    except (NotADraftError, AlreadyPublishedError) as exc:
        raise _conflict(exc) from exc
    except ValidationError as exc:
        raise _unprocessable(exc) from exc
    return WorkflowPublic.from_definition(published)
