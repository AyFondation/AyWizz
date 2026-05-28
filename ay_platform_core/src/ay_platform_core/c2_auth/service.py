# =============================================================================
# File: service.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c2_auth/service.py
# Description: C2 Auth Service facade. Orchestrates pluggable auth modes,
#              JWT issuance/verification, and user management.
#
#              v2: per-user preferences (trigram + LLM user prompt
#              override) and project-level system_prompt override.
#              `get_user_preferences` / `update_user_preferences` and
#              `update_project` resolve override-vs-default with the
#              C2-wide defaults from `AuthConfig`. `ProjectPublic`
#              responses now carry the EFFECTIVE `system_prompt` plus
#              an `is_default` flag so the UX has everything it needs
#              in a single round-trip.
#
# @relation implements:R-100-030
# @relation implements:R-100-038
# @relation implements:R-100-073
# =============================================================================

from __future__ import annotations

import contextlib
import os
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, status

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.gitea_client import GiteaClient, GiteaError
from ay_platform_core.c2_auth.models import (
    AuthConfigResponse,
    BrandConfig,
    DevCredential,
    FeatureFlags,
    JWTClaims,
    LoginRequest,
    ProjectCreate,
    ProjectPublic,
    ProjectUpdate,
    RBACProjectRole,
    ResetPasswordRequest,
    SessionInfo,
    TenantCreate,
    TenantPublic,
    TokenResponse,
    UserCreateRequest,
    UserInternal,
    UserPreferencesResponse,
    UserPreferencesUpdate,
    UserPublic,
    UserStatus,
    UserUpdateRequest,
    UXConfigResponse,
)
from ay_platform_core.c2_auth.modes.base import AuthMode
from ay_platform_core.c2_auth.modes.local_mode import LocalMode
from ay_platform_core.c2_auth.modes.none_mode import NoneMode
from ay_platform_core.c2_auth.modes.sso_mode import SSOMode

_FORBIDDEN_ENVIRONMENTS = {"production", "staging"}


class AuthService:
    """Facade for C2 Auth Service operations.

    Owns JWT issuance, verification, session tracking, and user CRUD.
    Auth mode selection is pluggable (none / local / sso). R-100-030.

    @relation implements:R-100-030
    @relation implements:R-100-038
    """

    def __init__(
        self,
        config: AuthConfig,
        repo: AuthRepository | None = None,
        gitea: GiteaClient | None = None,
    ) -> None:
        # R-100-032: fail fast if none mode is used in prod/staging.
        if (
            config.auth_mode == "none"
            and config.platform_environment in _FORBIDDEN_ENVIRONMENTS
        ):
            raise RuntimeError(
                f"Auth mode 'none' is forbidden in "
                f"'{config.platform_environment}' environment. "
                "Set AUTH_MODE=local or AUTH_MODE=sso."
            )
        self._config = config
        self._repo = repo
        self._gitea = gitea
        self._mode: AuthMode = self._build_mode()

    # ---- Internal helpers ---------------------------------------------------

    def _build_mode(self) -> AuthMode:
        match self._config.auth_mode:
            case "none":
                return NoneMode(self._config)
            case "local":
                if self._repo is None:
                    raise RuntimeError("AuthRepository required for auth_mode='local'")
                return LocalMode(self._repo)
            case "sso":
                return SSOMode()

    def _signing_key(self) -> str:
        if self._config.jwt_algorithm == "HS256":
            return self._config.jwt_secret_key
        return self._config.jwt_private_key

    def _verification_key(self) -> str:
        if self._config.jwt_algorithm == "HS256":
            return self._config.jwt_secret_key
        return self._config.jwt_public_key

    def _sign_jwt(self, claims: JWTClaims) -> str:
        payload = claims.model_dump(mode="json")
        return jwt.encode(payload, self._signing_key(), algorithm=self._config.jwt_algorithm)

    def _decode_jwt(self, token: str) -> JWTClaims:
        try:
            payload = jwt.decode(
                token,
                self._verification_key(),
                algorithms=[self._config.jwt_algorithm],
                audience="platform",
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
            ) from None
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}"
            ) from exc
        return JWTClaims(**payload)

    def _require_repo(self) -> AuthRepository:
        if self._repo is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This operation requires auth_mode='local'",
            )
        return self._repo

    # ---- Public API ---------------------------------------------------------

    def config_response(self) -> AuthConfigResponse:
        return AuthConfigResponse(auth_mode=self._config.auth_mode)

    def ux_config_response(self) -> UXConfigResponse:
        """Bootstrap config served at `GET /ux/config` for the Next.js
        frontend. Read from `AuthConfig.ux_*` fields (env-tunable
        without rebuild).

        `dev_credentials` is populated ONLY when both
        `auth_mode == 'local'` AND `ux_dev_mode_enabled == True` —
        defense-in-depth, two independent flags. Outside dev the
        field stays None (omitted from JSON) so prod responses are
        identical to v1.
        """
        return UXConfigResponse(
            api_version="v1",
            # Build stamp read straight from the container ENV (set by
            # `Dockerfile.api` via the BUILD_VERSION build-arg). Not a
            # Pydantic-settings field — see `config.py` for the
            # rationale.
            build_version=os.environ.get("BUILD_VERSION", "dev"),
            auth_mode=self._config.auth_mode,
            brand=BrandConfig(
                name=self._config.ux_brand_name,
                short_name=self._config.ux_brand_short_name,
                accent_color_hex=self._config.ux_brand_accent_color,
            ),
            features=FeatureFlags(
                chat_enabled=self._config.ux_feature_chat_enabled,
                kg_enabled=self._config.ux_feature_kg_enabled,
                cross_tenant_enabled=self._config.ux_feature_cross_tenant_enabled,
                file_download_enabled=self._config.ux_feature_file_download_enabled,
            ),
            dev_credentials=self._dev_credentials(),
        )

    def _dev_credentials(self) -> list[DevCredential] | None:
        """Build the dev-credentials list when (and only when) dev
        mode is on AND auth_mode is local. Returns None otherwise so
        `UXConfigResponse.dev_credentials` is omitted from the JSON
        payload entirely."""
        cfg = self._config
        if cfg.auth_mode != "local":
            return None
        if not cfg.ux_dev_mode_enabled:
            return None
        return [
            DevCredential(
                username=cfg.demo_seed_superroot_username,
                password=cfg.demo_seed_superroot_password,
                role_label="super-root (tenant_manager)",
                note="Content-blind: lifecycle ops only, no project access.",
            ),
            DevCredential(
                username=cfg.demo_seed_tenant_admin_username,
                password=cfg.demo_seed_tenant_admin_password,
                role_label="tenant admin",
                note=f"Admin of tenant {cfg.demo_seed_tenant_id!r}.",
            ),
            DevCredential(
                username=cfg.demo_seed_project_editor_username,
                password=cfg.demo_seed_project_editor_password,
                role_label="project editor (read/write)",
                note=f"On project {cfg.demo_seed_project_id!r}.",
            ),
            DevCredential(
                username=cfg.demo_seed_project_viewer_username,
                password=cfg.demo_seed_project_viewer_password,
                role_label="project viewer (read-only)",
                note=f"On project {cfg.demo_seed_project_id!r}.",
            ),
        ]

    async def issue_token(self, request: LoginRequest) -> TokenResponse:
        """Authenticate credentials and return a signed JWT. R-100-038."""
        user = await self._mode.authenticate(request)
        now = datetime.now(UTC)
        jti = str(uuid.uuid4())
        exp_ts = int(now.timestamp()) + self._config.token_ttl_seconds
        expires_at = datetime.fromtimestamp(exp_ts, tz=UTC)

        # Fetch project-scoped roles if a repository is available.
        project_scopes_raw: dict[str, list[str]] = {}
        if self._repo is not None:
            project_scopes_raw = await self._repo.get_project_scopes(user.user_id)

        project_scopes: dict[str, list[RBACProjectRole]] = {
            pid: [RBACProjectRole(r) for r in roles]
            for pid, roles in project_scopes_raw.items()
        }

        claims = JWTClaims(
            sub=user.user_id,
            iat=int(now.timestamp()),
            exp=exp_ts,
            jti=jti,
            auth_mode=self._config.auth_mode,
            tenant_id=user.tenant_id,
            roles=user.roles,
            project_scopes=project_scopes,
            username=user.username,
            name=user.name,
            email=user.email,
        )
        token = self._sign_jwt(claims)

        # Persist session for stateful revocation (none mode skips DB).
        if self._repo is not None:
            await self._repo.insert_session(jti, user.user_id, now, expires_at)

        return TokenResponse(access_token=token, expires_in=self._config.token_ttl_seconds)

    async def verify_token(self, token: str) -> JWTClaims:
        """Decode and validate JWT, checking active session. R-100-073."""
        claims = self._decode_jwt(token)

        # Stateful revocation check: jti must exist and be active. (A-6 plan)
        if self._repo is not None:
            session = await self._repo.get_session(claims.jti)
            if session is None or not session.get("active", False):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session has been revoked",
                )
        return claims

    async def logout(self, jti: str) -> None:
        """Mark session inactive (token remains syntactically valid until exp)."""
        if self._repo is not None:
            await self._repo.deactivate_session(jti)

    # ---- User CRUD (local mode only) ----------------------------------------

    async def create_user(self, request: UserCreateRequest) -> UserPublic:
        repo = self._require_repo()
        user_id = str(uuid.uuid4())
        argon2id_hash = LocalMode.hash_password(request.password)
        user = UserInternal(
            user_id=user_id,
            username=request.username,
            tenant_id=request.tenant_id,
            roles=request.roles,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
            name=request.name,
            email=request.email,
            argon2id_hash=argon2id_hash,
        )
        await repo.insert_user(user)
        return UserPublic.model_validate(user.model_dump())

    async def get_user(self, user_id: str) -> UserPublic:
        repo = self._require_repo()
        user = await repo.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserPublic.model_validate(user.model_dump())

    async def update_user(self, user_id: str, request: UserUpdateRequest) -> UserPublic:
        repo = self._require_repo()
        existing = await repo.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        patch: dict[str, object] = {}
        if request.roles is not None:
            patch["roles"] = [r.value for r in request.roles]
        if request.status is not None:
            patch["status"] = request.status.value
        if request.name is not None:
            patch["name"] = request.name
        if request.email is not None:
            patch["email"] = request.email

        if patch:
            await repo.update_user(user_id, patch)

        updated = await repo.get_user_by_id(user_id)
        if updated is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserPublic.model_validate(updated.model_dump())

    async def disable_user(self, user_id: str) -> None:
        repo = self._require_repo()
        existing = await repo.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        await repo.update_user(user_id, {"status": UserStatus.DISABLED.value})

    async def reset_password(self, user_id: str, request: ResetPasswordRequest) -> None:
        """Admin-only password reset. No self-service in v1. R-100-035."""
        repo = self._require_repo()
        existing = await repo.get_user_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        new_hash = LocalMode.hash_password(request.new_password)
        await repo.update_user(user_id, {"argon2id_hash": new_hash})
        await repo.reset_failed_attempts(user_id)

    # ---- Session management (admin only) ------------------------------------

    async def list_sessions(self) -> list[SessionInfo]:
        repo = self._require_repo()
        return await repo.list_active_sessions()

    async def revoke_session(self, session_id: str) -> None:
        repo = self._require_repo()
        await repo.deactivate_session(session_id)

    # ---- Tenant lifecycle (tenant_manager only) -----------------------------

    async def create_tenant(self, payload: TenantCreate) -> TenantPublic:
        repo = self._require_repo()
        if await repo.get_tenant(payload.tenant_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"tenant {payload.tenant_id!r} already exists",
            )
        now = datetime.now(UTC)
        await repo.insert_tenant(payload.tenant_id, payload.name, now)
        return TenantPublic(tenant_id=payload.tenant_id, name=payload.name, created_at=now)

    async def list_tenants(self) -> list[TenantPublic]:
        repo = self._require_repo()
        rows = await repo.list_tenants()
        return [
            TenantPublic(
                tenant_id=r["_key"],
                name=r["name"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def delete_tenant(self, tenant_id: str) -> None:
        repo = self._require_repo()
        if not await repo.delete_tenant(tenant_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"tenant {tenant_id!r} not found",
            )

    # ---- Project lifecycle (admin / tenant_admin) ---------------------------

    def _project_doc_to_public(self, doc: dict[str, Any]) -> ProjectPublic:
        """Render an Arango project document into a `ProjectPublic`
        response, resolving the `system_prompt` field against the
        C2-wide default. Absence of the key in the stored doc → use
        default + flag `is_default=True`. `git_repo_url` propagates
        as-is (None when Gitea provisioning hadn't run yet)."""
        override = doc.get("system_prompt")
        if override is None:
            effective = self._config.default_project_prompt
            is_default = True
        else:
            effective = override
            is_default = False
        return ProjectPublic(
            project_id=doc["_key"],
            tenant_id=doc["tenant_id"],
            name=doc["name"],
            profile=doc.get("profile", "code"),
            created_at=datetime.fromisoformat(doc["created_at"]),
            created_by=doc["created_by"],
            system_prompt=effective,
            system_prompt_is_default=is_default,
            git_repo_url=doc.get("git_repo_url"),
        )

    async def create_project(
        self, payload: ProjectCreate, tenant_id: str, actor_id: str
    ) -> ProjectPublic:
        repo = self._require_repo()
        # Ensure tenant exists — projects can't dangle in a deleted tenant.
        if await repo.get_tenant(tenant_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"tenant {tenant_id!r} not found; create it first",
            )
        if await repo.get_project(payload.project_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"project {payload.project_id!r} already exists",
            )

        # Provision the Gitea backing repo BEFORE inserting the Arango
        # project record (R-200-141). If provisioning fails, the
        # Arango row never lands → no orphan project. On success the
        # repo URL is persisted with the project so the UX can surface
        # it without a second round-trip.
        git_repo_url: str | None = None
        if self._gitea is not None:
            git_repo_url = await self._provision_gitea_for_project(
                project_id=payload.project_id,
                tenant_id=tenant_id,
                project_name=payload.name,
            )

        now = datetime.now(UTC)
        try:
            await repo.insert_project(
                payload.project_id, tenant_id, payload.name, now, actor_id,
                profile=payload.profile,
                git_repo_url=git_repo_url,
            )
        except Exception:
            # Best-effort rollback : if the Arango insert fails after
            # Gitea succeeded, tear down the Gitea repo so the next
            # retry doesn't 409 on the user-account creation.
            if self._gitea is not None:
                await self._rollback_gitea_for_project(
                    project_id=payload.project_id, tenant_id=tenant_id,
                )
            raise
        doc = await repo.get_project(payload.project_id)
        assert doc is not None  # just inserted
        return self._project_doc_to_public(doc)

    # ---- Gitea provisioning (R-200-141..142) -------------------------------

    @staticmethod
    def _gitea_service_account(tenant_id: str, project_id: str) -> str:
        """Compute the Gitea service-account username from
        (tenant, project). Gitea allows letters, digits, dot, dash,
        underscore — our tenant_id / project_id already conform to a
        compatible subset (validated at C2 model layer). Format :
        `svc-{tenant}-{project}` keeps the user list human-scannable."""
        return f"svc-{tenant_id}-{project_id}"

    async def _provision_gitea_for_project(
        self, *, project_id: str, tenant_id: str, project_name: str,
    ) -> str | None:
        """Create the service-account user + private repo for one
        project. Persist the credentials in `c2_project_secrets`.
        Returns the HTTPS clone URL on success ; on Gitea failure we
        raise an HTTPException so the FastAPI handler returns 502 to
        the caller (the project creation aborts). The Gitea outage
        scenario is preferable to a half-provisioned project."""
        assert self._gitea is not None  # narrowed in caller
        repo = self._require_repo()
        username = self._gitea_service_account(tenant_id, project_id)
        # Random password for the service-account. v1 keeps a single
        # password ; Pass 2.2 will derive a per-project OAuth token
        # from this credential and rotate it. uuid4 gives 122 bits of
        # entropy — comfortably above the brute-force horizon for the
        # internal Gitea instance.
        password = uuid.uuid4().hex
        email = f"{username}@aywizz.local"
        try:
            await self._gitea.create_user(
                username=username, password=password, email=email,
            )
            gitea_repo = await self._gitea.create_repo(
                owner=username,
                name=project_id,
                description=(
                    f"Backing repo for AyWizz project {project_name!r} "
                    f"(tenant {tenant_id!r})."
                ),
                private=True,
            )
        except GiteaError as exc:
            # Failed mid-way ; tear down anything that did get
            # created so a retry starts from a clean slate.
            await self._rollback_gitea_for_project(
                project_id=project_id, tenant_id=tenant_id,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"git backend provisioning failed: {exc}",
            ) from exc
        # Persist the credential. v1 stores plaintext ; Q-100-020
        # tracks the prod migration to a vault.
        await repo.upsert_project_secret(
            project_id,
            {
                "gitea_username": username,
                "gitea_password": password,
                "gitea_repo_full_name": gitea_repo.full_name,
            },
        )
        return gitea_repo.clone_url

    async def _rollback_gitea_for_project(
        self, *, project_id: str, tenant_id: str,
    ) -> None:
        """Best-effort cleanup after a half-completed provisioning.
        Deletes the user (with `purge=true` so any repo created under
        it goes with it). Never raises — any error here is logged via
        the GiteaError chain and the original failure propagates."""
        if self._gitea is None:
            return
        username = self._gitea_service_account(tenant_id, project_id)
        # Swallow GiteaError — the original creation error is what we
        # want to surface to the caller, not a cleanup secondary.
        with contextlib.suppress(GiteaError):
            await self._gitea.delete_user(username)
        repo = self._require_repo()
        await repo.delete_project_secret(project_id)

    async def get_project(self, project_id: str, tenant_id: str) -> ProjectPublic:
        repo = self._require_repo()
        doc = await repo.get_project(project_id)
        if doc is None or doc["tenant_id"] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        return self._project_doc_to_public(doc)

    async def list_projects(self, tenant_id: str) -> list[ProjectPublic]:
        repo = self._require_repo()
        rows = await repo.list_projects(tenant_id)
        return [self._project_doc_to_public(r) for r in rows]

    async def update_project(
        self, project_id: str, tenant_id: str, payload: ProjectUpdate,
    ) -> ProjectPublic:
        """Partial update of a project. Currently mutates `name` and
        `system_prompt`. For `system_prompt`, the semantics are :
          - field omitted (`None`) → no change ;
          - empty string `""`     → clear the override (UNSET the
                                    Arango field so reads fall back
                                    to the C2 default and the
                                    `is_default` flag reverts to True);
          - non-empty string      → persist as the project's override.
        """
        repo = self._require_repo()
        doc = await repo.get_project(project_id)
        if doc is None or doc["tenant_id"] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        patch: dict[str, Any] = {}
        if payload.name is not None:
            patch["name"] = payload.name
        if payload.system_prompt is not None and payload.system_prompt != "":
            patch["system_prompt"] = payload.system_prompt
        if patch:
            await repo.update_project(project_id, patch)
        if payload.system_prompt == "":
            await repo.unset_project_field(project_id, "system_prompt")
        refreshed = await repo.get_project(project_id)
        assert refreshed is not None
        return self._project_doc_to_public(refreshed)

    async def delete_project(self, project_id: str, tenant_id: str) -> None:
        repo = self._require_repo()
        existing = await repo.get_project(project_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        if existing["tenant_id"] != tenant_id:
            # Treat cross-tenant access as 404 to avoid leaking project
            # existence to a foreign tenant.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        await repo.delete_project(project_id)

    async def grant_project_member(
        self,
        project_id: str,
        tenant_id: str,
        user_id: str,
        role: RBACProjectRole,
    ) -> None:
        repo = self._require_repo()
        project = await repo.get_project(project_id)
        if project is None or project["tenant_id"] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        target_user = await repo.get_user_by_id(user_id)
        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"user {user_id!r} not found",
            )
        if target_user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user belongs to a different tenant",
            )
        await repo.grant_project_role(user_id, project_id, role.value)

    async def revoke_project_member(
        self, project_id: str, tenant_id: str, user_id: str
    ) -> None:
        repo = self._require_repo()
        project = await repo.get_project(project_id)
        if project is None or project["tenant_id"] != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"project {project_id!r} not found",
            )
        if not await repo.revoke_project_role(user_id, project_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"user {user_id!r} has no role on project {project_id!r}"
                ),
            )

    # ---- User preferences (self-service, any authenticated user) ------------

    @staticmethod
    def _is_valid_trigram(value: str) -> bool:
        """Trigram rules : 3-4 alphanumeric chars. Mirror the regex
        the UI enforces in `lib/preferences.ts` so the server and
        client validate identically (cheap defence-in-depth — the
        UX is authoritative on display, the server is authoritative
        on persistence)."""
        if not 3 <= len(value) <= 4:
            return False
        return value.isalnum() and value.isascii()

    @staticmethod
    def _is_valid_hex_color(value: str) -> bool:
        """Hex colour rule : `#RRGGBB` (case-insensitive). Short form
        `#RGB` and 8-digit RGBA are intentionally rejected — the UI
        renders the bubble + avatar with this colour at full opacity,
        and accepting alpha would let a user submit a near-invisible
        tint."""
        if len(value) != 7 or value[0] != "#":
            return False
        return all(c in "0123456789abcdefABCDEF" for c in value[1:])

    def _prefs_doc_to_response(
        self, doc: dict[str, Any] | None,
    ) -> UserPreferencesResponse:
        """Resolve a (possibly absent) stored prefs document into the
        effective response shape. `trigram` and `user_color` are
        exposed as-stored (None when no override) ; `user_prompt` is
        the override OR the C2 default, with `user_prompt_is_default`
        flagging the latter so the UI can render a 'Reset to default'
        button only when meaningful."""
        stored_trigram = (doc or {}).get("trigram")
        stored_user_prompt = (doc or {}).get("user_prompt")
        stored_user_color = (doc or {}).get("user_color")
        if stored_user_prompt is None:
            user_prompt = self._config.default_user_prompt
            is_default = True
        else:
            user_prompt = stored_user_prompt
            is_default = False
        return UserPreferencesResponse(
            trigram=stored_trigram if isinstance(stored_trigram, str) else None,
            user_prompt=user_prompt,
            user_prompt_is_default=is_default,
            user_color=(
                stored_user_color if isinstance(stored_user_color, str) else None
            ),
        )

    async def get_user_preferences(self, user_id: str) -> UserPreferencesResponse:
        repo = self._require_repo()
        doc = await repo.get_user_preferences(user_id)
        return self._prefs_doc_to_response(doc)

    async def update_user_preferences(
        self, user_id: str, payload: UserPreferencesUpdate,
    ) -> UserPreferencesResponse:
        """Merge-update the user's prefs. Semantics per field :
          - omitted (`None`)       → no change.
          - empty string `""`      → clear override (delete the field
                                     from the stored doc).
          - non-empty string       → persist as override.
        Trigram non-empty values are validated (3-4 alnum) ; invalid
        values raise 400 BAD_REQUEST."""
        repo = self._require_repo()
        patch: dict[str, Any] = {}
        if payload.trigram is not None:
            if payload.trigram == "":
                patch["trigram"] = None  # signal: drop from doc
            elif self._is_valid_trigram(payload.trigram):
                patch["trigram"] = payload.trigram
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="trigram must be 3-4 alphanumeric characters",
                )
        if payload.user_prompt is not None:
            if payload.user_prompt == "":
                patch["user_prompt"] = None  # signal: drop from doc
            else:
                patch["user_prompt"] = payload.user_prompt
        if payload.user_color is not None:
            if payload.user_color == "":
                patch["user_color"] = None  # signal: drop from doc
            elif self._is_valid_hex_color(payload.user_color):
                patch["user_color"] = payload.user_color.lower()
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="user_color must be a 7-char hex like '#3b82f6'",
                )
        if patch:
            await repo.upsert_user_preferences(user_id, patch)
        refreshed = await repo.get_user_preferences(user_id)
        return self._prefs_doc_to_response(refreshed)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _build_cached_service() -> AuthService:
    """Build and cache the singleton AuthService from environment config."""
    config = AuthConfig()
    repo: AuthRepository | None = None
    if config.auth_mode != "none":
        repo = AuthRepository.from_config(
            config.arango_url,
            config.arango_db,
            config.arango_username,
            config.arango_password,
        )
    return AuthService(config, repo)


def get_service() -> AuthService:
    """FastAPI dependency. Override via app.dependency_overrides in tests."""
    return _build_cached_service()
