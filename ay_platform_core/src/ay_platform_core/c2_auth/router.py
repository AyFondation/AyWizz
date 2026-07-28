# =============================================================================
# File: router.py
# Version: 6
# Path: ay_platform_core/src/ay_platform_core/c2_auth/router.py
# Description: FastAPI APIRouter for C2 Auth Service. 12 endpoints covering
#              authentication, token verification, logout, user management,
#              and session administration.
#
#              v3 (2026-06-01, E-100-002 fix): `/verify` forward-auth now
#              propagates the caller's PROJECT-SCOPED role for the project
#              named in the forwarded request URI (`X-Forwarded-Uri`), in
#              addition to global roles. Before this it emitted only global
#              roles, so every project-scoped gate (e.g. C7
#              `POST /sources/upload`, requiring project_editor/owner/admin)
#              was effectively global-admin-only and a project_editor got a
#              spurious 403. No cross-project leak: only the request's
#              project role is added.
#              v4 (E-100-002 v4): `/verify` now also enforces project
#              LIFECYCLE STATUS on content URIs — an `inactive` project
#              refuses all access, an `archived` project refuses mutations
#              (read-only freeze), independently of role. Governance URIs
#              (admin, project metadata, ACL) are exempt. Record-derived
#              project scoping (C3/C4 by-id) is out of scope here (inc3b).
#              v6 (E-100-002 v7): per-user CRUD (`/users` create + `/users/{id}`
#              get/update/delete/reset-password) is TENANT-ISOLATED — an
#              `admin`/`tenant_admin` is confined to its own tenant. Create
#              forces the caller's `tenant_id`; the {user_id} routes 403 on a
#              cross-tenant target (`_require_same_tenant_user`). Closes a
#              cross-tenant provisioning/oversight hole.
#
# @relation implements:R-100-039
# @relation implements:R-100-040
# @relation implements:R-100-041
# =============================================================================

from __future__ import annotations

import re
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm

from ay_platform_core.c2_auth.models import (
    AuthConfigResponse,
    JWTClaims,
    LoginRequest,
    ProjectStatus,
    RBACGlobalRole,
    ResetPasswordRequest,
    SessionInfo,
    TokenResponse,
    UserCreateRequest,
    UserPublic,
    UserUpdateRequest,
)
from ay_platform_core.c2_auth.service import AuthService, get_service

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer()


# ---------------------------------------------------------------------------
# Shared dependencies
# ---------------------------------------------------------------------------


async def _get_current_claims(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    service: AuthService = Depends(get_service),
) -> JWTClaims:
    return await service.verify_token(credentials.credentials)


def _require_admin(claims: JWTClaims = Depends(_get_current_claims)) -> JWTClaims:
    if RBACGlobalRole.ADMIN not in claims.roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return claims


def _require_admin_or_tenant_admin(
    claims: JWTClaims = Depends(_get_current_claims),
) -> JWTClaims:
    if not {RBACGlobalRole.ADMIN, RBACGlobalRole.TENANT_ADMIN} & set(claims.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin or tenant_admin role required",
        )
    return claims


async def _require_same_tenant_user(
    user_id: str,
    claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> JWTClaims:
    """Tenant-isolation gate for per-user CRUD (E-100-002 v7). An `admin`
    (= tenant_admin) is confined to its OWN tenant: the target user SHALL
    belong to the caller's tenant (404 if absent, 403 if cross-tenant)."""
    target = await service.get_user(user_id)  # 404 if absent
    if target.tenant_id != claims.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user is not in your tenant",
        )
    return claims


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------


@router.api_route(
    "/config", methods=["GET", "HEAD"], response_model=AuthConfigResponse
)
async def get_config(service: AuthService = Depends(get_service)) -> AuthConfigResponse:
    """Return current auth mode. No authentication required.

    HEAD is supported for connectivity / liveness probes that don't
    want a body — Starlette strips the body automatically when the
    method is HEAD.
    """
    return service.config_response()


@router.post("/token", response_model=TokenResponse)
async def token_grant(
    form_data: OAuth2PasswordRequestForm = Depends(),
    service: AuthService = Depends(get_service),
) -> TokenResponse:
    """OAuth2 password grant (form-encoded). Rate-limited by gateway. R-100-039."""
    request = LoginRequest(username=form_data.username, password=form_data.password)
    return await service.issue_token(request)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    service: AuthService = Depends(get_service),
) -> TokenResponse:
    """Platform-native JSON login. Rate-limited by gateway. R-100-039."""
    return await service.issue_token(body)


# ---------------------------------------------------------------------------
# Authenticated endpoints
# ---------------------------------------------------------------------------


_PROJECT_URI_RE = re.compile(r"/projects/([^/?#]+)")


def _project_id_from_uri(uri: str) -> str | None:
    """Extract `{project_id}` from a project-scoped forwarded URI, e.g.
    `/api/v1/memory/projects/{pid}/sources/upload` or
    `/api/v1/projects/{pid}/...`. Returns None for non-project paths
    (e.g. `/api/v1/projects` list, `/api/v1/conversations`)."""
    match = _PROJECT_URI_RE.search(uri)
    return unquote(match.group(1)) if match else None


# Methods that WRITE. Under an `archived` (read-only-frozen) project these are
# refused while reads still pass; under `inactive` every method is refused.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _content_project_id(uri: str) -> str | None:
    """Return the `{project_id}` of a project-scoped **content** request, or
    `None` when the URI is not project content.

    E-100-002 v4 separates a project's *governance* surface (its metadata,
    lifecycle status, ACL) from its *content* (requirements, sources, runs,
    artifacts, …). Lifecycle-status enforcement applies to CONTENT only — an
    operator must still reach a non-`active` project's governance to
    reactivate it, and an owner must still see that their project is frozen.
    Governance URIs excluded here:
      * anything under `/admin/…` (platform-operator surface);
      * the bare project resource `/api/v1/projects/{pid}` (metadata CRUD);
      * `/api/v1/projects/{pid}/members…` (the project ACL).
    Every other `…/projects/{pid}/<sub-resource>` is content."""
    path = uri.split("?", 1)[0].split("#", 1)[0]
    if "/admin/" in path:
        return None
    match = _PROJECT_URI_RE.search(path)
    if match is None:
        return None
    remainder = path[match.end() :]
    if remainder in ("", "/") or remainder.startswith("/members"):
        return None
    return unquote(match.group(1))


def _forward_auth_roles(claims: JWTClaims, request: Request) -> str:
    """Build the `X-User-Roles` forward-auth value (E-100-002).

    Global roles ALWAYS apply. Additionally, when the original request
    (Traefik forwards it as `X-Forwarded-Uri`) targets a project-scoped
    path, the caller's role FOR THAT SPECIFIC project is appended — so a
    `project_editor` / `project_owner` can act on EVERY project they hold
    a scope on, and ONLY those: a different project's id yields a different
    (or empty) scope lookup, so there is no cross-project leak. Before this,
    forward-auth emitted only the global roles, making every project-scoped
    gate effectively global-admin-only."""
    roles: list[str] = [r.value for r in claims.roles]
    project_id = _project_id_from_uri(request.headers.get("X-Forwarded-Uri", ""))
    if project_id is not None:
        roles.extend(r.value for r in claims.project_scopes.get(project_id, []))
    return ",".join(roles)


@router.get("/verify", response_model=JWTClaims)
async def verify(
    request: Request,
    response: Response,
    claims: JWTClaims = Depends(_get_current_claims),
    service: AuthService = Depends(get_service),
) -> JWTClaims:
    """Verify bearer token, return parsed claims, and emit Traefik forward-auth
    headers — these are picked up by Traefik's forward-auth middleware and
    injected into the request forwarded to backend services. Backends rely
    on `X-User-Id`, `X-User-Roles`, AND `X-Tenant-Id` (some require all
    three; missing `X-Tenant-Id` triggers 401 on tenant-scoped routes).

    `X-User-Roles` carries the caller's global roles PLUS, when the
    forwarded request targets `…/projects/{pid}/…`, their project-scoped
    role for that project (E-100-002) — so project_editor/owner can act on
    every project they hold a scope on (and only those).

    **Project lifecycle enforcement (E-100-002 v4).** When the forwarded
    request targets project *content* (see `_content_project_id`), the target
    project's `status` is resolved and access is refused at this boundary,
    independently of the caller's role: an `inactive` project blocks every
    method, an `archived` project blocks mutating methods only (read-only
    freeze). Governance URIs (admin, project metadata, ACL) are never blocked,
    so an operator can still reactivate and an owner can still see the freeze.
    Record-derived project scoping (C3 conversations, C4 run-by-id — no
    `{project_id}` in the URL) is NOT covered here; it is tracked as inc3b.
    """
    content_pid = _content_project_id(request.headers.get("X-Forwarded-Uri", ""))
    if content_pid is not None:
        project_status = await service.get_project_status(content_pid)
        if project_status is not None and project_status is not ProjectStatus.ACTIVE:
            method = request.headers.get("X-Forwarded-Method", "GET").upper()
            blocked = project_status is ProjectStatus.INACTIVE or (
                project_status is ProjectStatus.ARCHIVED
                and method in _MUTATING_METHODS
            )
            if blocked:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"project is {project_status.value}",
                )

    response.headers["X-User-Id"] = claims.sub
    response.headers["X-User-Roles"] = _forward_auth_roles(claims, request)
    response.headers["X-Platform-Auth-Mode"] = claims.auth_mode
    if claims.tenant_id:
        response.headers["X-Tenant-Id"] = claims.tenant_id
    return claims


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    claims: JWTClaims = Depends(_get_current_claims),
    service: AuthService = Depends(get_service),
) -> None:
    """Invalidate the current session (jti). Token remains syntactically valid until exp."""
    await service.logout(claims.jti)


# ---------------------------------------------------------------------------
# User management (admin / tenant_admin only)
# ---------------------------------------------------------------------------


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Create a new user (local mode only). R-100-034. E-100-002 v7: the new
    user is FORCED into the caller's tenant — an admin cannot provision users
    in another tenant."""
    scoped = body.model_copy(update={"tenant_id": claims.tenant_id})
    return await service.create_user(scoped)


@router.get("/users/{user_id}", response_model=UserPublic)
async def get_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_same_tenant_user),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Retrieve user by ID. Hash excluded. R-100-012. Own-tenant only (v7)."""
    return await service.get_user(user_id)


@router.patch("/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    _claims: JWTClaims = Depends(_require_same_tenant_user),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Update user roles, status, or display fields. Own-tenant only (v7)."""
    return await service.update_user(user_id, body)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_same_tenant_user),
    service: AuthService = Depends(get_service),
) -> None:
    """Soft-delete user (sets status=disabled). Own-tenant only (v7)."""
    await service.disable_user(user_id)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    _claims: JWTClaims = Depends(_require_same_tenant_user),
    service: AuthService = Depends(get_service),
) -> None:
    """Admin-triggered password reset. No self-service in v1. R-100-035.
    Own-tenant only (v7)."""
    await service.reset_password(user_id, body)


# ---------------------------------------------------------------------------
# Session management (admin only)
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(
    _claims: JWTClaims = Depends(_require_admin),
    service: AuthService = Depends(get_service),
) -> list[SessionInfo]:
    """List active sessions (admin only)."""
    return await service.list_sessions()


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    _claims: JWTClaims = Depends(_require_admin),
    service: AuthService = Depends(get_service),
) -> None:
    """Revoke a specific session by jti (admin only)."""
    await service.revoke_session(session_id)
