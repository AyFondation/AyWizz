# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c6_validation/router.py
# Description: FastAPI APIRouter for C6 per 700-SPEC §8. Forward-auth headers
#              (X-User-Id, X-User-Roles, X-Tenant-Id) are injected by C1/C2
#              and consumed here for RBAC gating.
#
#              v2 (2026-10-05): the run-trigger route moves from
#              `POST /api/v1/validation/runs` to
#              `POST /api/v1/projects/{project_id}/validation/runs`, and
#              `"admin"` leaves its `required` tuple. The old shape was
#              unreachable by every caller — see `trigger_run`'s docstring
#              for the full chain. The `"admin"` entry was dead text
#              (`_require_role` strips it) that made the 403 misleading, and
#              it contradicted the route catalogue, which already declared
#              `accept_global_roles=()`.
#
# @relation implements:R-700-010
# @relation implements:R-700-012
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status

from ay_platform_core.c6_validation.models import (
    DomainList,
    Finding,
    FindingPage,
    PluginDescriptor,
    RunTriggerRequest,
    RunTriggerResponse,
    ValidationRun,
)
from ay_platform_core.c6_validation.service import ValidationService, get_service

router = APIRouter(tags=["validation"])

# ---------------------------------------------------------------------------
# RBAC helpers — identical pattern to C3/C4/C5/C7
# ---------------------------------------------------------------------------


def _require_actor(x_user_id: str | None = Header(default=None)) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header missing (forward-auth not applied)",
        )
    return x_user_id


# E-100-002 v7: content-blind global roles — stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})


def _require_role(
    x_user_roles: str | None,
    required: tuple[str, ...],
) -> None:
    roles = {r.strip() for r in (x_user_roles or "").split(",") if r.strip()}
    roles -= _CONTENT_BLIND_GLOBAL_ROLES
    if not roles.intersection(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"requires one of: {', '.join(required)}",
        )


# ---------------------------------------------------------------------------
# Plugin / domain discovery
# ---------------------------------------------------------------------------


@router.get("/api/v1/validation/plugins", response_model=list[PluginDescriptor])
async def list_plugins(
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> list[PluginDescriptor]:
    """Every installed validation plugin, with the checks it registers.

    Read this first: a plugin's `domain` is what `POST .../runs`
    accepts, and its `checks` are the `check_id` values a `Finding`
    will cite back at you.
    """
    return service.list_plugins()


@router.get("/api/v1/validation/domains", response_model=DomainList)
async def list_domains(
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> DomainList:
    """The domains that have a plugin installed.

    The same information as `GET /plugins` reduced to the one field a
    caller needs to trigger a run, for a client that only wants to
    populate a selector.
    """
    return DomainList(domains=service.list_domains())


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/projects/{project_id}/validation/runs",
    response_model=RunTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_run(
    project_id: str,
    payload: RunTriggerRequest,
    _user: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    service: ValidationService = Depends(get_service),
) -> RunTriggerResponse:
    """Trigger a validation run on `{project_id}`.

    **WHY THE PROJECT ID IS IN THE PATH.** It used to live only in the body,
    at `POST /api/v1/validation/runs`, and that made this endpoint
    **unreachable by every caller**. C2's forward-auth appends the caller's
    project-scoped role to `X-User-Roles` only when the forwarded URI matches
    `…/projects/{pid}/…` (it sees headers and the URI, never the body);
    `_require_role` then strips the content-blind global roles
    (E-100-002 v7). With no project id in the URI, the role set was empty for
    everyone and the gate returned 403 unconditionally — the `"admin"` in the
    `required` tuple below was dead text that made the 403 message
    actively misleading. The system tier caught it; no other tier could,
    because the e2e auth matrix injects `X-User-Roles` itself and so
    fabricates the very header whose derivation was broken.

    Moving the id into the path follows the convention already used five
    times over (`…/artifacts`, `…/git`, `…/documents`, `…/requirements`,
    `…/backups`) and additionally brings this endpoint under C2's project
    lifecycle enforcement (`_content_project_id`), which it previously
    escaped: a run could be triggered on an `inactive` or `archived`
    project.

    **THE PATH IS THE AUTHORITY.** `payload.project_id` stays required —
    dropping it would turn `project_id` optional throughout the model and
    ripple `str | None` across every service use site for no functional
    gain — but it SHALL equal the path. Gating on the path and then acting
    on the body is the confused-deputy pattern: the caller would prove a
    role on one project and have the run executed against another. A
    mismatch is refused, never silently reconciled.
    """
    if payload.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"project_id mismatch: path {project_id!r} vs body "
                f"{payload.project_id!r}. The path is authoritative — it is "
                "what the authorization gate was applied to."
            ),
        )
    # Triggering a validation run is a project-level action — allowed for
    # editors and owners ON THIS PROJECT. Global admin/tenant_admin are
    # content-blind (E-100-002 v7) and are stripped by `_require_role`.
    _require_role(x_user_roles, required=("project_editor", "project_owner"))
    return await service.trigger_run(
        payload,
        requirements=payload.requirements,
        artifacts=payload.artifacts,
        tenant_id=x_tenant_id or "",
        user_id=_user,
    )


@router.get(
    "/api/v1/validation/runs/{run_id}",
    response_model=ValidationRun,
)
async def get_run(
    run_id: str,
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> ValidationRun:
    """One run's status and aggregate result.

    Poll this until `status` leaves `pending` and `running`. Findings
    are only complete once it has: asking for them earlier returns what
    has been recorded so far, which is not the same as none.
    """
    return await service.get_run(run_id)


@router.get(
    "/api/v1/validation/runs/{run_id}/findings",
    response_model=FindingPage,
)
async def list_findings(
    run_id: str,
    limit: int = 100,
    offset: int = 0,
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> FindingPage:
    """One run's findings, paginated.

    `total` counts the whole run rather than this page, so a client
    knows whether to ask again. `limit` is capped at 1000 and a value
    outside [1, 1000] is refused with 400 rather than clamped — a
    silently clamped page makes a caller believe it has seen
    everything.
    """
    if limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be in [1, 1000]",
        )
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="offset must be >= 0"
        )
    return await service.list_findings(run_id, limit=limit, offset=offset)


@router.get(
    "/api/v1/validation/findings/{finding_id}",
    response_model=Finding,
)
async def get_finding(
    finding_id: str,
    _user: str = Depends(_require_actor),
    service: ValidationService = Depends(get_service),
) -> Finding:
    """One finding, by id.

    For following a finding cited elsewhere — a run report, a trace, an
    MCP tool result — without paging through its run.
    """
    return await service.get_finding(finding_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/validation/health",
    response_model=None,
)
async def health(
    service: ValidationService = Depends(get_service),
) -> dict[str, str]:
    """Liveness. Answers `ok` whenever the process serves requests.

    It does NOT check the plugin registry or the store: a readiness
    probe that fails on a dependency takes the pod out of service for a
    condition restarting it will not fix.
    """
    _ = service
    return {"status": "ok"}
