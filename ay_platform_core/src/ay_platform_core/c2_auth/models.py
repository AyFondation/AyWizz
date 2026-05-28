# =============================================================================
# File: models.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c2_auth/models.py
# Description: Pydantic v2 models for C2 Auth Service public contracts.
#              JWTClaims implements E-100-001.
#              RBACGlobalRole / RBACProjectRole implement E-100-002 v2 —
#              5-role hierarchy: tenant_manager (super-root, no content),
#              admin (tenant-scoped admin, alias of tenant_admin),
#              project_owner / project_editor / project_viewer.
#
#              v5: per-user `UserPreferencesUpdate` / `UserPreferencesResponse`
#              and per-project `ProjectUpdate` + `system_prompt` /
#              `system_prompt_is_default` on `ProjectPublic`. Both follow
#              the same effective-value-plus-is-default-flag pattern
#              so the UI can render a single textarea with an
#              "Override active" / "Reset to default" affordance.
#
#              v4: adds `DevCredential` + optional `dev_credentials`
#              field on `UXConfigResponse` for the demo-seed auto-fill
#              affordance (gated server-side by `ux_dev_mode_enabled`,
#              must remain `None` in production).
#
#              v3: Tenant + Project lifecycle models added (Phase A of the
#              v1 functional plan). Tenants are owned by `tenant_manager`;
#              projects are owned by `admin` / `tenant_admin` of the
#              hosting tenant.
#
# @relation implements:E-100-001
# @relation implements:E-100-002
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RBACGlobalRole(StrEnum):
    """Global platform roles embedded in JWT claims.roles. E-100-002 v2.

    Hierarchy (top to bottom):
      - TENANT_MANAGER: super-root. Cross-tenant operations (create/list/
        delete tenants, grant/revoke tenant admins). SHALL NOT have access
        to tenant content (conversations, projects, requirements, etc.).
      - ADMIN / TENANT_ADMIN: tenant-scoped admin. Creates projects in
        their tenant, grants project_owner roles, full read/write within
        their tenant boundary. ADMIN and TENANT_ADMIN are synonyms;
        ADMIN is the canonical name in spec, TENANT_ADMIN is retained
        for backwards-compat with v1 code.
      - USER: baseline authenticated user (no special grants).

    Project-scoped roles (project_owner / project_editor / project_viewer)
    live in `RBACProjectRole`, embedded under `JWTClaims.project_scopes`.

    @relation implements:E-100-002
    """

    TENANT_MANAGER = "tenant_manager"
    ADMIN = "admin"
    TENANT_ADMIN = "tenant_admin"
    USER = "user"


class RBACProjectRole(StrEnum):
    """Per-project roles embedded in JWT claims.project_scopes. E-100-002.

    @relation implements:E-100-002
    """

    OWNER = "project_owner"
    EDITOR = "project_editor"
    VIEWER = "project_viewer"


class UserStatus(StrEnum):
    """Lifecycle status of a platform user account."""

    ACTIVE = "active"
    DISABLED = "disabled"


class JWTClaims(BaseModel):
    """Platform-internal JWT claim set. All auth modes emit this structure.

    `sub` is the stable user_id ; `username` is the human-readable
    login name (distinct from `name`, the display name). The UX
    surfaces username in nav / dashboards ; `sub` is used as a
    fallback when no login name is set (none-mode test fixtures).

    @relation implements:E-100-001
    @relation implements:R-100-038
    """

    iss: Literal["platform-auth"] = "platform-auth"
    sub: str
    aud: str = "platform"
    iat: int
    exp: int
    jti: str
    auth_mode: Literal["none", "local", "sso"]
    tenant_id: str
    roles: list[RBACGlobalRole]
    project_scopes: dict[str, list[RBACProjectRole]] = Field(default_factory=dict)
    username: str | None = None
    name: str | None = None
    email: str | None = None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """JSON body for POST /auth/login."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """Successful authentication response."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class AuthConfigResponse(BaseModel):
    """Response for GET /auth/config (public endpoint)."""

    auth_mode: Literal["none", "local", "sso"]


# ---------------------------------------------------------------------------
# UX bootstrap config — public endpoint that the Next.js frontend hits
# at startup so it can self-configure (feature flags, brand, auth mode)
# WITHOUT a rebuild. The actual API base URL is provided to the UX via
# a static `runtime-config.json` mounted as a K8s ConfigMap (different
# concern: deployment-time wiring, not runtime feature config). This
# endpoint covers everything that can change PER PLATFORM ENVIRONMENT
# without redeploying the frontend image.
# ---------------------------------------------------------------------------


class BrandConfig(BaseModel):
    """Brand identity served to the UX. Override via `C2_UX_BRAND_*`
    env vars to skin the platform per tenant / per environment without
    rebuilding the frontend bundle."""

    name: str
    short_name: str
    accent_color_hex: str


class FeatureFlags(BaseModel):
    """Capability toggles the UX checks before showing UI affordances.
    All flags default to True for v1 platforms; flip to False per
    deployment to hide features that aren't ready or aren't licensed.

    `cross_tenant_enabled` defaults to False because the underlying
    server-side feature is itself deferred (gap UX #4 — spec
    amendment required)."""

    chat_enabled: bool
    kg_enabled: bool
    cross_tenant_enabled: bool
    file_download_enabled: bool


class DevCredential(BaseModel):
    """A single demo-seed credential surfaced to the UX login page for
    auto-fill. Returned ONLY when both `auth_mode == 'local'` AND
    `ux_dev_mode_enabled == True` server-side. Production MUST leave
    that flag False — well-known passwords have no place there."""

    username: str
    password: str
    role_label: str = Field(
        description="Human-readable role for display (e.g. "
        "'super-root', 'tenant admin', 'project editor', "
        "'project viewer').",
    )
    note: str | None = Field(
        default=None,
        description="Optional hint shown alongside the credential "
        "(e.g. content-blind warning for super-root).",
    )


class UXConfigResponse(BaseModel):
    """Bootstrap config served to the UX on startup.

    The UX's bootstrap sequence is:
      1. Fetch `/runtime-config.json` (static, K8s-ConfigMap-mounted)
         → discover `apiBaseUrl`.
      2. Fetch `<apiBaseUrl>/ux/config` (this response) → discover
         brand, feature flags, auth mode.
      3. Render shell ; fetch component-specific data (LLM models,
         project list, etc.) lazily as the user navigates.
    """

    api_version: str
    build_version: str = Field(
        default="dev",
        description="API tier image build stamp. Baked at docker build "
        "time and surfaced here so the UX footer can show the version "
        "the API is running — quickest way to confirm a rebuild took "
        "effect.",
    )
    auth_mode: Literal["none", "local", "sso"]
    brand: BrandConfig
    features: FeatureFlags
    dev_credentials: list[DevCredential] | None = Field(
        default=None,
        description="Demo-seed credentials for auto-fill on the login "
        "page. None outside dev mode ; populated only when "
        "`C2_UX_DEV_MODE_ENABLED=true`.",
    )


# ---------------------------------------------------------------------------
# User management models
# ---------------------------------------------------------------------------


class UserPublic(BaseModel):
    """User data safe for external exposure. Hash and lock fields excluded."""

    user_id: str
    username: str
    tenant_id: str
    roles: list[RBACGlobalRole]
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime
    name: str | None = None
    email: str | None = None


class UserInternal(UserPublic):
    """Full user record including credential hash. Never leaves repository layer."""

    model_config = ConfigDict(populate_by_name=True)

    argon2id_hash: str
    failed_attempts: int = 0
    locked_until: datetime | None = None


class UserCreateRequest(BaseModel):
    """Request body for POST /auth/users."""

    username: str
    password: str
    tenant_id: str
    roles: list[RBACGlobalRole] = Field(default_factory=lambda: [RBACGlobalRole.USER])
    name: str | None = None
    email: str | None = None


class UserUpdateRequest(BaseModel):
    """Request body for PATCH /auth/users/{user_id}."""

    roles: list[RBACGlobalRole] | None = None
    status: UserStatus | None = None
    name: str | None = None
    email: str | None = None


class ResetPasswordRequest(BaseModel):
    """Request body for POST /auth/users/{user_id}/reset-password."""

    new_password: str


class SessionInfo(BaseModel):
    """Active session record for admin visibility."""

    session_id: str
    user_id: str
    issued_at: datetime
    expires_at: datetime
    active: bool


# ---------------------------------------------------------------------------
# Tenant lifecycle models — owned by `tenant_manager`
# ---------------------------------------------------------------------------


class TenantCreate(BaseModel):
    """Request body for POST /admin/tenants."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class TenantPublic(BaseModel):
    """Tenant record for external exposure."""

    tenant_id: str
    name: str
    created_at: datetime


class TenantList(BaseModel):
    items: list[TenantPublic]


# ---------------------------------------------------------------------------
# Project lifecycle models — owned by `admin` / `tenant_admin`
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    """Request body for POST /api/v1/projects.

    `profile` selects the production-domain pipeline (C4 orchestrator
    plugin). v1 ships `code` only ; future profiles (`data`, `doc`,
    etc.) plug in via the C4 registry without changing this schema.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    profile: str = Field(default="code", min_length=2, max_length=32)


class ProjectUpdate(BaseModel):
    """Request body for PATCH /api/v1/projects/{pid} — partial update.
    Every field is optional ; only the provided ones are mutated."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    system_prompt: str | None = Field(
        default=None,
        max_length=4000,
        description="Project-level prompt addendum applied AFTER the "
        "user-level prompt and BEFORE the RAG context block. Empty "
        "string clears the override (falls back to the C2 default).",
    )


class ProjectPublic(BaseModel):
    """Project record for external exposure.

    `system_prompt` is the EFFECTIVE value the UX should display in the
    settings page and the chat client should forward to C3 — it is the
    per-project override if set, else the C2-wide
    `default_project_prompt` (may be empty when both are empty).
    `system_prompt_is_default` is True when no project-level override
    is stored, so the UI can render a "Using default" badge and offer
    a "Reset to default" affordance only when meaningful.

    `git_repo_url` is the HTTPS clone URL of the project's Gitea repo
    (R-200-142). Set when Gitea provisioning succeeded at project
    creation ; absent (`None`) for projects created before Gitea
    landed (backwards-compat). Read-only — the UX displays it as a
    copyable string ; updates happen only via re-provisioning."""

    project_id: str
    tenant_id: str
    name: str
    profile: str = "code"
    created_at: datetime
    created_by: str
    system_prompt: str = Field(
        default="",
        description="Effective project prompt addendum — override if "
        "set, else the C2 default. May be empty.",
    )
    system_prompt_is_default: bool = Field(
        default=True,
        description="True when no per-project override is stored "
        "(`system_prompt` came from the C2 default).",
    )
    git_repo_url: str | None = Field(
        default=None,
        description="HTTPS clone URL of the project's Gitea repo "
        "(R-200-142). None on legacy projects created before the "
        "Gitea integration shipped.",
    )


class ProjectList(BaseModel):
    items: list[ProjectPublic]


class ProjectMemberGrant(BaseModel):
    """Request body for POST /api/v1/projects/{pid}/members/{uid}.
    The role to grant is sent in the body so the path stays clean."""

    model_config = ConfigDict(extra="forbid")

    role: RBACProjectRole


# ---------------------------------------------------------------------------
# User preferences — per-user UX + LLM behavioural overrides. Stored
# server-side (collection `c2_user_preferences`) so they survive
# browser / device changes. v1 fields : trigram avatar override, user
# prompt override applied before any other LLM instruction.
# ---------------------------------------------------------------------------


class UserPreferencesUpdate(BaseModel):
    """Request body for PUT /api/v1/users/me/preferences. Every field
    is optional ; missing keys leave the stored value untouched. An
    explicit empty string clears the override (falls back to default).
    `null` is equivalent to omitting the field (no change)."""

    model_config = ConfigDict(extra="forbid")

    trigram: str | None = Field(default=None, max_length=4)
    """Override trigram. Empty string clears the override (UI-derived
    default takes over). Length is enforced at service layer (3-4
    alphanumeric) — accept any length here so a clearing empty string
    is not rejected by min_length."""

    user_prompt: str | None = Field(default=None, max_length=4000)
    """Override user prompt. Empty string clears the override (C2
    default takes over)."""

    user_color: str | None = Field(default=None, max_length=9)
    """Override accent colour for the user's chat bubble + avatar
    (hex `#RRGGBB`). Empty string clears the override (UI falls back
    to its built-in default palette). Validated at service layer."""


class UserPreferencesResponse(BaseModel):
    """Response of GET /api/v1/users/me/preferences — the EFFECTIVE
    values (override OR system default) plus boolean flags telling
    the UI whether the user has explicitly overridden each field, so
    the preferences page can render a 'Reset to default' affordance."""

    model_config = ConfigDict(extra="forbid")

    trigram: str | None
    """Stored trigram override, or null when the user relies on the
    UI-derived default. The UI keeps the localStorage fallback for
    pre-login renders, but server is authoritative."""

    user_prompt: str
    """Effective user prompt — override if set, else C2 default. Never
    empty (the C2 default is non-empty by design)."""

    user_prompt_is_default: bool
    """True when `user_prompt` is the C2 default (no per-user
    override). The UI shows a 'Reset to default' button only when
    False."""

    user_color: str | None
    """Stored accent colour override (hex `#RRGGBB`), or null when no
    override is set. The UI applies a default palette in that case ;
    multi-user conversations can render per-user bubble tints once
    project sharing lands."""
