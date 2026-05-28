# =============================================================================
# File: config.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c2_auth/config.py
# Description: Configuration for C2 Auth Service.
#
#              v4: adds the **demo seed** + **UX dev-mode** envelope. Two
#              independent flags drive a defense-in-depth pattern :
#                - `demo_seed_enabled`         seeds DB with a test
#                                              tenant + 4 users + 1
#                                              project + 2 grants.
#                - `ux_dev_mode_enabled`       exposes those credentials
#                                              on /ux/config so the UX
#                                              login page can render an
#                                              auto-fill panel.
#              Both default False ; production overlays SHALL leave
#              both False. Local stack overlay flips both to True.
#
#              v3: env-var single-source refactor (R-100-110 v2, R-100-111 v2).
#              Facts that are platform-wide identical (Arango URL, DB name,
#              shared application credentials, OS-level switches) are now
#              read from UNPREFIXED env vars via `validation_alias`. Only
#              fields that legitimately differ between components keep the
#              `C2_` prefix (auth mode, JWT keys, token TTL).
#
#              v2: harmonised env-var naming under the C2_ prefix.
#              v1: initial.
#
# @relation implements:R-100-111
# @relation implements:R-100-112
# @relation implements:R-100-110
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    """Settings for C2 Auth Service, loaded from environment.

    Priority: init kwargs > env vars > defaults.
    Per-component fields use the ``C2_`` prefix; shared fields use
    ``validation_alias`` to read unprefixed names.
    """

    # populate_by_name=True so model_validate(...) accepts field names
    # alongside the validation_alias for shared knobs.
    model_config = SettingsConfigDict(
        env_prefix="c2_", extra="ignore", populate_by_name=True
    )

    # ---- Platform-wide (read without prefix via validation_alias) -----------
    platform_environment: Literal["development", "testing", "staging", "production"] = (
        Field(default="development", validation_alias="PLATFORM_ENVIRONMENT")
    )

    # NOTE : the API tier's build stamp is intentionally NOT a
    # Pydantic-settings field. It is baked into the container ENV by
    # `Dockerfile.api` (`ENV BUILD_VERSION=${BUILD_VERSION}`) and read
    # directly via `os.environ` in `service.py`. Two reasons :
    #   - keeping it out of `AuthConfig` keeps it out of the env-file
    #     completeness coherence check ; we explicitly don't want it
    #     declared in `.env.test` / `.env.example` because the env-file
    #     value would WIN over the Dockerfile ENV (docker compose's
    #     env_file directive overrides image ENV) and flatten the
    #     per-build distinction we're after.
    #   - the value is infra metadata, not application config — the
    #     application's behaviour does not branch on it.

    # ---- Gitea backend (R-200-140..145) ----------------------------------
    # Connection details for the bundled Gitea instance. C2 talks to
    # it via HTTP Basic auth using the root admin creds bootstrapped
    # by `gitea_init` (compose). Production overlays SHALL migrate to
    # per-deployment tokens stored in a vault — Q-100-020 tracks the
    # threat-model upgrade.
    gitea_base_url: str = Field(
        default="http://gitea:3000",
        validation_alias="C2_GITEA_BASE_URL",
        description="Base URL of the bundled Gitea instance. Empty "
        "disables provisioning (legacy stack without Gitea).",
    )
    gitea_admin_username: str = Field(
        default="aywizz",
        validation_alias="GITEA_ROOT_USERNAME",
        description="Root admin username for the bundled Gitea.",
    )
    gitea_admin_password: str = Field(
        default="change-me-gitea-root-password",
        validation_alias="GITEA_ROOT_PASSWORD",
        description="Root admin password for the bundled Gitea.",
    )

    # Shared ArangoDB connection — every component talks to the same cluster
    # and the same logical database; ownership boundaries are enforced at the
    # collection level (R-100-012 v3) and via the dedicated app user.
    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(
        default="changeme", validation_alias="ARANGO_PASSWORD"
    )

    # ---- C2-specific (C2_ prefix) ------------------------------------------
    auth_mode: Literal["none", "local", "sso"] = Field(
        default="none",
        description="Pluggable auth mode. R-100-030.",
    )

    jwt_algorithm: Literal["HS256", "RS256", "EdDSA"] = Field(
        default="HS256",
        description="Signing algorithm. HS256 for dev, RS256/EdDSA for prod. R-100-038.",
    )
    jwt_secret_key: str = Field(
        default="change-me-in-production-min-32-chars!!",
        description="Symmetric key for HS256. Must be >=32 chars.",
    )
    jwt_private_key: str = Field(
        default="",
        description="PEM-encoded private key for RS256/EdDSA signing.",
    )
    jwt_public_key: str = Field(
        default="",
        description="PEM-encoded public key for RS256/EdDSA verification.",
    )
    token_ttl_seconds: int = Field(
        default=3600,
        description="Token lifetime in seconds. Default 1h per E-100-001.",
    )

    # Pre-existing admin user bootstrapped by the C2 lifespan when
    # `auth_mode == "local"`. Ignored in `none` / `sso` modes — but the
    # values are still declared here so the env file stays exhaustive
    # (R-100-110 v2). R-100-118 v2.
    local_admin_username: str = Field(
        default="admin",
        description="Username of the bootstrap admin (auth_mode=local). "
        "Granted global ADMIN role (= tenant_admin scope per E-100-002 v2).",
    )
    local_admin_password: str = Field(
        default="changeme",
        description="Password of the bootstrap admin (auth_mode=local).",
    )

    # Pre-existing tenant_manager (super-root) user bootstrapped by the
    # C2 lifespan when `auth_mode == "local"` AND both fields are
    # non-empty. The tenant_manager is content-blind per E-100-002 v2:
    # tenant lifecycle (create / list / delete tenants) ONLY, no
    # access to projects / sources / conversations / etc. Empty fields
    # → no tenant_manager bootstrap (admin alone suffices for
    # single-tenant setups).
    local_tenant_manager_username: str = Field(
        default="",
        description="Username of the bootstrap tenant_manager "
        "(auth_mode=local, both _USERNAME and _PASSWORD set).",
    )
    local_tenant_manager_password: str = Field(
        default="",
        description="Password of the bootstrap tenant_manager.",
    )

    # ---- UX bootstrap config (served via GET /ux/config) ------------------
    # These fields drive the Next.js frontend's runtime self-
    # configuration — change them via env vars to skin / toggle the
    # platform per deployment without rebuilding the UI bundle.
    ux_brand_name: str = Field(
        default="AyWizz Platform",
        description="Full brand name displayed in the UX header.",
    )
    ux_brand_short_name: str = Field(
        default="AyWizz",
        description="Short brand name for tabs, mobile, etc.",
    )
    ux_brand_accent_color: str = Field(
        default="#3b82f6",
        description="Primary accent color (hex with leading #).",
    )
    # ---- LLM behavioural defaults --------------------------------------------
    # Default user-level system prompt prepended to every chat message
    # for users who haven't overridden it via /preferences. The user
    # may override per-account (stored in c2_user_preferences).
    default_user_prompt: str = Field(
        default=(
            "Do not invent things. Do not cheat. Do not lie. "
            "If you don't know, say so. "
            "If you are missing information, ask for it."
        ),
        description="System default user prompt prepended to chat. "
        "Override per-user via PUT /api/v1/users/me/preferences.",
    )
    # Default project-level addendum applied to every conversation in
    # the project. Empty by default ; admins / project_owners can set
    # one per project via PATCH /api/v1/projects/{pid}.
    default_project_prompt: str = Field(
        default="",
        description="System default project prompt addendum. Empty "
        "means no per-project override applied. Per-project override "
        "via PATCH /api/v1/projects/{pid}.",
    )

    ux_feature_chat_enabled: bool = Field(default=True)
    ux_feature_kg_enabled: bool = Field(default=True)
    ux_feature_cross_tenant_enabled: bool = Field(
        default=False,
        description="Cross-tenant source promotion — server-side "
        "feature deferred (gap UX #4); UX hides the affordance "
        "until this flips True.",
    )
    ux_feature_file_download_enabled: bool = Field(default=True)

    # ---- UX dev mode (separate from demo seed) ----------------------------
    # When True, /ux/config exposes the demo credentials so the login
    # page renders an auto-fill panel. Independent of demo_seed_enabled
    # (defense-in-depth) : staging may seed without exposing ; prod
    # leaves both False.
    ux_dev_mode_enabled: bool = Field(
        default=False,
        description="Expose demo credentials on /ux/config for auto-fill "
        "in the UX login page. PRODUCTION SHALL leave this False.",
    )

    # ---- Demo seed (manual-test bootstrap data) ---------------------------
    # When demo_seed_enabled=True AND auth_mode=='local', the C2
    # lifespan creates a complete test scenario : 1 tenant + 4 users
    # (super-root, tenant-admin, project-editor, project-viewer) + 1
    # project + 2 project grants. Idempotent. PRODUCTION SHALL leave
    # this False — demo accounts have well-known passwords by design.
    demo_seed_enabled: bool = Field(
        default=False,
        description="Bootstrap a complete test scenario on C2 startup. "
        "PRODUCTION SHALL leave this False (well-known passwords).",
    )
    demo_seed_tenant_id: str = Field(
        default="tenant-test",
        description="Tenant id created by the demo seed.",
    )
    demo_seed_tenant_name: str = Field(default="Test Tenant")
    demo_seed_project_id: str = Field(
        default="project-test",
        description="Project id created by the demo seed under tenant-test.",
    )
    demo_seed_project_name: str = Field(default="Test Project")
    # Second demo project — DocGen profile. Same tenant + grants as
    # `project-test`, but `profile=docgen` so the UI shows the
    # simplified sidebar (Overview / Sources / Conversations /
    # Documents / Settings — no Pipeline / Validation / Requirements).
    demo_seed_docgen_project_id: str = Field(
        default="project-docgen",
        description="DocGen demo project id created under the demo tenant.",
    )
    demo_seed_docgen_project_name: str = Field(default="Demo DocGen Project")
    # Per-user credentials. Defaults are intentionally non-trivial yet
    # well-known so they can be auto-filled from /ux/config without
    # secret-leak risk (prod has demo_seed_enabled=False anyway).
    demo_seed_superroot_username: str = Field(default="superroot")
    demo_seed_superroot_password: str = Field(default="dev-superroot")
    demo_seed_tenant_admin_username: str = Field(default="tenant-admin")
    demo_seed_tenant_admin_password: str = Field(default="dev-tenant")
    demo_seed_project_editor_username: str = Field(default="project-editor")
    demo_seed_project_editor_password: str = Field(default="dev-editor")
    demo_seed_project_viewer_username: str = Field(default="project-viewer")
    demo_seed_project_viewer_password: str = Field(default="dev-viewer")
