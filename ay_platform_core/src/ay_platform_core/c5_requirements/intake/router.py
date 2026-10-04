# =============================================================================
# File: router.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c5_requirements/intake/router.py
# Description: REST surface for supplied requirement intake — 310-SPEC §4.4 /
#              §4.5.
#
#              Role gates:
#                - ingesting a drop and recording findings are authoring
#                  work: `project_editor` or `project_owner`;
#                - VERIFYING a degraded extraction is an owner decision. It
#                  is an assertion that our OCR reading matches what the
#                  customer actually sent, and everything downstream —
#                  splitting, allocation, coverage — rests on it
#                  (R-310-061).
#
#              Proposing a split is editor work: the model and the service
#              already refuse an unjustified or lossy one, so the gate does
#              not have to carry that weight too.
#
#              Reads are merely authenticated: a team cannot review what it
#              cannot see.
#
# @relation implements:R-310-060
# @relation implements:R-310-061
# @relation implements:R-310-063
# @relation implements:R-310-092
# @relation implements:R-310-093
# @relation implements:R-310-094
# @relation implements:R-310-095
# =============================================================================

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError

from .extraction import ExtractionError
from .intervals import IntervalError, TextInterval
from .models import (
    FindingListResponse,
    FindingRequest,
    FragmentListResponse,
    IngestResponse,
    ReworkListResponse,
    SourceFormat,
    SplitRequest,
    SplitResponse,
    SuppliedRequirement,
    SuppliedRequirementListResponse,
    VerificationResponse,
)
from .service import IntakeRefusedError, IntakeService
from .storage import IntakePathError, SuppliedRequirementImmutableError

router = APIRouter(tags=["intake"])

_BASE = "/api/v1/projects/{project_id}/intake/drops/{drop_id}"

# E-100-002 v7: content-blind global roles are stripped before a content gate.
_CONTENT_BLIND_GLOBAL_ROLES = frozenset({"admin", "tenant_admin"})

_AUTHORS = ("project_editor", "project_owner")
_OWNERS = ("project_owner",)


def get_intake_service(request: Request) -> IntakeService:
    """FastAPI dependency resolved from app.state.intake_service."""
    service = getattr(request.app.state, "intake_service", None)
    if service is None:  # pragma: no cover - wiring error, not a runtime path
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="intake service not configured",
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


def _unreadable(exc: ExtractionError) -> HTTPException:
    # 422, not 400: the request was well-formed, the FILE is not what it
    # claimed to be — or an anchor no longer resolves against the stored
    # extraction, which is a data integrity fault the caller must see.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


def _refused(exc: Exception) -> HTTPException:
    # 409: the request is well-formed and the caller entitled; the PROCESS
    # refuses it. The message names the rule so the workbench can explain.
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _invalid(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
    )


def _bad_path(exc: IntakePathError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ---------------------------------------------------------------------------
# Ingest — R-310-060 / R-310-062
# ---------------------------------------------------------------------------


@router.post(
    _BASE, response_model=IngestResponse, status_code=status.HTTP_201_CREATED
)
async def ingest_drop(
    project_id: str,
    drop_id: str,
    file: UploadFile = File(...),
    source_format: SourceFormat = Form(...),
    needed_ocr: bool = Form(default=False),
    _actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: IntakeService = Depends(get_intake_service),
) -> IngestResponse:
    """Read a supplied drop and store what it contains."""
    _require_role(x_user_roles, _AUTHORS)
    payload = await file.read()
    try:
        report = await service.ingest(
            project_id,
            drop_id,
            filename=file.filename or "drop",
            payload=payload,
            source_format=source_format,
            content_type=file.content_type or "application/octet-stream",
            needed_ocr=needed_ocr,
        )
    except ExtractionError as exc:
        raise _unreadable(exc) from exc
    except SuppliedRequirementImmutableError as exc:
        raise _refused(exc) from exc
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return IngestResponse(
        drop_id=report.drop_id,
        source_format=report.source_format,
        source_class=report.source_class,
        requirement_ids=list(report.requirement_ids),
        needs_verification=report.needs_verification,
    )


@router.post(f"{_BASE}/verify", response_model=VerificationResponse)
async def verify_extraction(
    project_id: str,
    drop_id: str,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: IntakeService = Depends(get_intake_service),
) -> VerificationResponse:
    """Confirm that a degraded drop's extracted text matches what was sent.

    Owner-gated: everything downstream — splitting, allocation, coverage —
    rests on this assertion (R-310-061).
    """
    _require_role(x_user_roles, _OWNERS)
    try:
        cleared = await service.verify_extraction(project_id, drop_id, actor=actor)
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return VerificationResponse(cleared=list(cleared))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    f"{_BASE}/requirements", response_model=SuppliedRequirementListResponse
)
async def list_requirements(
    project_id: str,
    drop_id: str,
    _actor: str = Depends(_require_actor),
    service: IntakeService = Depends(get_intake_service),
) -> SuppliedRequirementListResponse:
    try:
        ids = await service._storage.list_requirement_ids(project_id, drop_id)
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return SuppliedRequirementListResponse(requirement_ids=ids)


@router.get(
    f"{_BASE}/requirements/{{requirement_id}}", response_model=SuppliedRequirement
)
async def get_requirement(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    _actor: str = Depends(_require_actor),
    service: IntakeService = Depends(get_intake_service),
) -> SuppliedRequirement:
    """Return one supplied requirement, with its anchor re-verified."""
    try:
        return await service._storage.get_requirement(
            project_id, drop_id, requirement_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ExtractionError as exc:
        raise _unreadable(exc) from exc
    except IntakePathError as exc:
        raise _bad_path(exc) from exc


# ---------------------------------------------------------------------------
# Quality findings — R-310-063
# ---------------------------------------------------------------------------


@router.post(
    f"{_BASE}/requirements/{{requirement_id}}/findings",
    response_model=FindingListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_finding(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    payload: FindingRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: IntakeService = Depends(get_intake_service),
) -> FindingListResponse:
    _require_role(x_user_roles, _AUTHORS)
    try:
        await service.record_finding(
            project_id,
            drop_id,
            requirement_id=requirement_id,
            criterion_id=payload.criterion_id,
            detail=payload.detail,
            actor=actor,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValidationError as exc:
        raise _invalid(exc) from exc
    except ExtractionError as exc:
        raise _unreadable(exc) from exc
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    findings = await service.findings_of(project_id, drop_id, requirement_id)
    return FindingListResponse(findings=list(findings))


@router.get(
    f"{_BASE}/requirements/{{requirement_id}}/findings",
    response_model=FindingListResponse,
)
async def list_findings(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    _actor: str = Depends(_require_actor),
    service: IntakeService = Depends(get_intake_service),
) -> FindingListResponse:
    try:
        findings = await service.findings_of(project_id, drop_id, requirement_id)
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return FindingListResponse(findings=list(findings))


# ---------------------------------------------------------------------------
# Splitting — R-310-092 / R-310-093 / R-310-094
# ---------------------------------------------------------------------------


@router.post(
    f"{_BASE}/requirements/{{requirement_id}}/split",
    response_model=SplitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def propose_split(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    payload: SplitRequest,
    actor: str = Depends(_require_actor),
    x_user_roles: str | None = Header(default=None),
    service: IntakeService = Depends(get_intake_service),
) -> SplitResponse:
    """Split a supplied requirement, and raise the defect with its issuer.

    The response carries BOTH, as the service returns both: a client cannot
    be shown the split without what is owed to the issuer (R-310-094).
    """
    _require_role(x_user_roles, _AUTHORS)
    try:
        spans = tuple(TextInterval(s.start, s.end) for s in payload.spans)
    except IntervalError as exc:
        raise _invalid(exc) from exc

    statements = tuple(s.statement or "" for s in payload.spans)
    try:
        proposal, rework = await service.propose_split(
            project_id,
            drop_id,
            requirement_id,
            spans=spans,
            actor=actor,
            statements=statements if all(statements) else None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except IntakeRefusedError as exc:
        raise _refused(exc) from exc
    except ValidationError as exc:
        # The proposal's own validator refused it — most often because the
        # spans do not tile the source, and the message quotes the text that
        # would have been lost (R-310-092).
        raise _invalid(exc) from exc
    except ExtractionError as exc:
        raise _unreadable(exc) from exc
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return SplitResponse(proposal=proposal, rework=rework)


@router.get(
    f"{_BASE}/requirements/{{requirement_id}}/fragments",
    response_model=FragmentListResponse,
)
async def list_fragments(
    project_id: str,
    drop_id: str,
    requirement_id: str,
    _actor: str = Depends(_require_actor),
    service: IntakeService = Depends(get_intake_service),
) -> FragmentListResponse:
    """Return a requirement's fragments, empty when it was never split."""
    try:
        fragments = await service.fragments_of(project_id, drop_id, requirement_id)
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return FragmentListResponse(fragments=list(fragments))


@router.get(f"{_BASE}/rework", response_model=ReworkListResponse)
async def list_rework(
    project_id: str,
    drop_id: str,
    _actor: str = Depends(_require_actor),
    service: IntakeService = Depends(get_intake_service),
) -> ReworkListResponse:
    """Return everything this drop owes its issuing party (R-310-094)."""
    try:
        requests = await service.rework_requests(project_id, drop_id)
    except IntakePathError as exc:
        raise _bad_path(exc) from exc
    return ReworkListResponse(requests=list(requests))
