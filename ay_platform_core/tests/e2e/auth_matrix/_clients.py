# =============================================================================
# File: _clients.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/_clients.py
# Description: Builds authenticated httpx.AsyncClients per role for the
#              auth matrix tests. Two header schemes coexist:
#                - C2 admin endpoints (POST /auth/users etc.) require a
#                  real Bearer JWT signed by the test C2 service.
#                - All other endpoints trust forward-auth headers
#                  (X-User-Id, X-User-Roles, X-Tenant-Id) — Traefik
#                  would normally derive these from the JWT. The tests
#                  send them directly to simulate post-forward-auth state.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI

from ay_platform_core.c2_auth.models import JWTClaims, RBACGlobalRole
from ay_platform_core.c2_auth.service import AuthService
from tests.e2e.auth_matrix._catalog import EndpointSpec


@dataclass(frozen=True)
class RoleProfile:
    """Identity + grants for one virtual user in the test matrix."""

    user_id: str
    tenant_id: str
    global_roles: tuple[str, ...] = ()
    """e.g. ('tenant_manager',), ('admin',), () for plain user."""

    project_id: str | None = None
    project_role: str | None = None
    """e.g. 'project_owner' on `project_id`. None = no project grant."""

    @property
    def role_label(self) -> str:
        """Stable parametrize id."""
        if self.global_roles:
            return "+".join(self.global_roles)
        if self.project_role:
            return self.project_role
        return "user"

    @property
    def x_user_roles(self) -> str:
        """X-User-Roles forward-auth header value (comma-separated).

        Includes both global roles AND the project role (if any) — the
        platform's downstream `_require_role` checks a flat list.
        Production Traefik propagates only the relevant grants for the
        request path; tests simulate that by NOT including project_role
        for cross-project access scenarios (see `for_other_project`).
        """
        parts: list[str] = list(self.global_roles)
        if self.project_role:
            parts.append(self.project_role)
        return ",".join(parts)


@dataclass(frozen=True)
class IdentityClients:
    """Group of clients per role for ONE component, sharing one tenant
    + project. Tests pick a (component, role) pair and call against the
    matching client."""

    component: str
    by_role: dict[str, httpx.AsyncClient]


def build_forward_auth_headers(profile: RoleProfile) -> dict[str, str]:
    """Headers as Traefik forward-auth would propagate them downstream."""
    return {
        "X-User-Id": profile.user_id,
        "X-Tenant-Id": profile.tenant_id,
        "X-User-Roles": profile.x_user_roles,
    }


async def build_bearer_headers(
    auth_service: AuthService, profile: RoleProfile
) -> dict[str, str]:
    """JWT bearer header signed by the test C2 service. Used for C2's
    own admin endpoints which validate `Authorization: Bearer <jwt>`
    via Depends(get_current_claims).

    The C2 verify path checks (1) JWT signature, (2) active session for
    the jti — so we BOTH sign a JWT AND insert a matching session row.
    Without the session insert, every forged-but-valid JWT yields 401
    "Session has been revoked".
    """
    project_scopes: dict[str, list[str]] = {}
    if profile.project_id and profile.project_role:
        project_scopes[profile.project_id] = [profile.project_role]

    role_strings = list(profile.global_roles) or ["user"]
    role_enums = [RBACGlobalRole(r) for r in role_strings]
    jti = f"jti-{profile.user_id}-{datetime.now(tz=UTC).timestamp()}"
    claims = JWTClaims(
        sub=profile.user_id,
        iat=int(datetime.now(tz=UTC).timestamp()),
        exp=10**12,
        jti=jti,
        auth_mode="local",
        tenant_id=profile.tenant_id,
        roles=role_enums,
        project_scopes=project_scopes,  # type: ignore[arg-type]
    )
    token = auth_service._sign_jwt(claims)
    # Persist session row so `verify_token`'s stateful revocation check
    # doesn't reject the forged JWT. Idempotent insertion: if the jti is
    # already there (parametrised tests share a profile), the repo's
    # insert_session is overwrite-safe via _key.
    repo = auth_service._repo
    if repo is not None:
        now = datetime.now(tz=UTC)
        expires_at = now.replace(year=now.year + 1)
        await repo.insert_session(jti, profile.user_id, now, expires_at)
    return {"Authorization": f"Bearer {token}"}


def build_anonymous_headers() -> dict[str, str]:
    """No identity at all — used to assert 401 on protected endpoints."""
    return {}


def needs_bearer(spec: EndpointSpec) -> bool:
    """Decides which auth scheme the endpoint expects.

    Bearer JWT (validated server-side via Depends):
      - `/auth/verify`, `/auth/logout`
      - `/auth/users/*`, `/auth/sessions/*`
      - `/admin/tenants/*` (Phase A — tenant_manager)

    Forward-auth headers (X-User-Id / X-Tenant-Id / X-User-Roles):
      - `/api/v1/projects/*` (Phase A — admin / project_owner)
      - All non-c2_auth components.

    Open endpoints (no auth at all):
      - `/auth/config`, `/auth/token`, `/auth/login`.
    """
    if spec.component != "c2_auth":
        return False
    # Projects router was deliberately built with forward-auth headers
    # to match the rest of the platform's downstream-facing endpoints.
    if spec.path.startswith("/api/v1/projects"):
        return False
    return spec.path not in ("/auth/config", "/auth/token", "/auth/login")


def make_asgi_client(app: FastAPI) -> httpx.AsyncClient:
    """Wraps a FastAPI app in an httpx AsyncClient (ASGI in-process).

    `raise_app_exceptions=False` is REQUIRED for the auth matrix: tests
    that fire requests at endpoints whose handlers raise (e.g. DELETE
    /auth/sessions/{id} on a non-existent session) should observe a
    response, not propagate the exception out of the test. The matrix
    is a black-box auth check — any non-401/403 response, including
    500, means the role gate cleared.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://e2e",
    )


__all__ = [
    "IdentityClients",
    "RoleProfile",
    "build_anonymous_headers",
    "build_bearer_headers",
    "build_forward_auth_headers",
    "make_asgi_client",
    "needs_bearer",
]
