# =============================================================================
# File: router.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c2_auth/router.py
# Description: FastAPI APIRouter for C2 Auth Service. 12 endpoints covering
#              authentication, token verification, logout, user management,
#              and session administration.
#
# @relation implements:R-100-039
# @relation implements:R-100-040
# @relation implements:R-100-041
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm

from ay_platform_core.c2_auth.models import (
    AuthConfigResponse,
    JWTClaims,
    LoginRequest,
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


@router.get("/verify", response_model=JWTClaims)
async def verify(
    response: Response,
    claims: JWTClaims = Depends(_get_current_claims),
) -> JWTClaims:
    """Verify bearer token, return parsed claims, and emit Traefik forward-auth
    headers — these are picked up by Traefik's forward-auth middleware and
    injected into the request forwarded to backend services. Backends rely
    on `X-User-Id`, `X-User-Roles`, AND `X-Tenant-Id` (some require all
    three; missing `X-Tenant-Id` triggers 401 on tenant-scoped routes).
    """
    response.headers["X-User-Id"] = claims.sub
    response.headers["X-User-Roles"] = ",".join(claims.roles)
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
    _claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Create a new user (local mode only). R-100-034."""
    return await service.create_user(body)


@router.get("/users/{user_id}", response_model=UserPublic)
async def get_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Retrieve user by ID. Hash excluded. R-100-012."""
    return await service.get_user(user_id)


@router.patch("/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    body: UserUpdateRequest,
    _claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> UserPublic:
    """Update user roles, status, or display fields."""
    return await service.update_user(user_id, body)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_user(
    user_id: str,
    _claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> None:
    """Soft-delete user (sets status=disabled). Irreversible via API in v1."""
    await service.disable_user(user_id)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    _claims: JWTClaims = Depends(_require_admin_or_tenant_admin),
    service: AuthService = Depends(get_service),
) -> None:
    """Admin-triggered password reset. No self-service in v1. R-100-035."""
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
