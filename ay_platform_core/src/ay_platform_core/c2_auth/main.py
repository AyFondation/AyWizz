# =============================================================================
# File: main.py
# Version: 6
# Path: ay_platform_core/src/ay_platform_core/c2_auth/main.py
# Description: FastAPI app factory for C2 Auth Service. Used by the
#              production container (uvicorn ay_platform_core.c2_auth.main:app)
#              and by e2e/system tests that want to spin a real HTTP surface.
#              Config is read from env-vars via AuthConfig. Arango collections
#              are bootstrapped during the lifespan; in `local` auth mode an
#              admin user is also bootstrapped from C2_LOCAL_ADMIN_*
#              (R-100-118 v2).
#              v6 (2026-10-09): `describe_app` enriches the
#              generated OpenAPI document — app description + real
#              version, the gateway-injected identity headers hidden
#              (the document was advertising them as caller-supplied),
#              and the derivable 401 / 404 responses declared.
#
#              v5: mounts the preferences_router at
#              `/api/v1/users/me/preferences` (any authenticated user,
#              minus platform_manager). Hosts the per-user trigram + LLM
#              `user_prompt` override.
#
#              v4: adds `_ensure_demo_seed()` for the manual-test stack
#              (gated by `C2_DEMO_SEED_ENABLED`). Pre-provisions a
#              complete scenario : 1 tenant (`tenant-test`), 4 users
#              (super-root / tenant-admin / project-editor /
#              project-viewer), 1 project (`project-test`), 2 project
#              grants. Idempotent ; runs after admin/platform_manager
#              bootstraps so the seeded users coexist with them.
#              Production overlays leave the flag False.
#
#              v3: mounts admin_router at `/admin` (tenant lifecycle,
#              platform_manager only) and projects_router at
#              `/api/v1/projects` (project lifecycle, admin / project_owner).
#
# @relation implements:R-100-030
# @relation implements:R-100-118
# =============================================================================

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from ay_platform_core.api_docs import describe_app, docs_urls
from ay_platform_core.c2_auth.admin_router import router as admin_router
from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.db.repository import AuthRepository
from ay_platform_core.c2_auth.gitea_client import GiteaClient
from ay_platform_core.c2_auth.models import (
    RBACGlobalRole,
    RBACProjectRole,
    UserInternal,
    UserStatus,
)
from ay_platform_core.c2_auth.modes.local_mode import LocalMode
from ay_platform_core.c2_auth.preferences_router import router as preferences_router
from ay_platform_core.c2_auth.projects_router import router as projects_router
from ay_platform_core.c2_auth.router import router
from ay_platform_core.c2_auth.service import AuthService
from ay_platform_core.c2_auth.service import get_service as c2_get_service
from ay_platform_core.c2_auth.ux_router import ux_router
from ay_platform_core.observability import (
    TraceContextMiddleware,
    configure_logging,
)
from ay_platform_core.observability.auth_guard import AuthGuardMiddleware
from ay_platform_core.observability.config import LoggingSettings

_log = logging.getLogger("c2_auth.bootstrap")


#: The tenant every locally-bootstrapped identity belongs to. A constant
#: rather than three string literals: the user doc, the tenant doc and the
#: platform-manager bootstrap must agree, and they drifted once already.
_BOOTSTRAP_TENANT = "default"


async def _ensure_bootstrap_tenant(repo: AuthRepository, cfg: AuthConfig) -> None:
    """Create the `default` TENANT document if it is absent.

    WHY THIS EXISTS. `_ensure_local_admin` has always inserted a user
    carrying `tenant_id="default"` without creating that tenant, so a
    freshly bootstrapped stack held a user belonging to a tenant that did
    not exist as a record. Nothing noticed while no code looked the tenant
    up — then `seed_e2e.py` began creating a project (v0.1.0-beta.14.19),
    `create_project` checked `get_tenant()` as it has since
    v0.1.0-beta.1, and the system-tests workflow went red with
    `tenant 'default' not found; create it first`.

    The repair belongs HERE and not in the seeder: creating a tenant is a
    `platform_manager` power (E-100-002), and the seeder authenticates as
    `admin` — it could not create one however it tried. More to the point,
    a bootstrap that mints a user into a tenant is the thing that owes that
    tenant's existence.

    Idempotent, and ordered BEFORE the user bootstraps so the tenant is
    never the missing half of a half-built stack.
    """
    if cfg.auth_mode != "local":
        return
    if await repo.get_tenant(_BOOTSTRAP_TENANT) is not None:
        _log.info("bootstrap tenant %r already present", _BOOTSTRAP_TENANT)
        return
    await repo.insert_tenant(
        _BOOTSTRAP_TENANT, "Default tenant", datetime.now(UTC)
    )
    _log.info("bootstrapped tenant %r", _BOOTSTRAP_TENANT)


async def _ensure_local_admin(repo: AuthRepository, cfg: AuthConfig) -> None:
    """Create the bootstrap admin user if `auth_mode == "local"` and absent.

    Idempotent: silently skips if a user with the configured username
    already exists. Roles default to global ADMIN.

    Assumes `_ensure_bootstrap_tenant` ran first — the user it writes
    belongs to that tenant.
    """
    if cfg.auth_mode != "local":
        return
    existing = await repo.get_user_by_username(cfg.local_admin_username)
    if existing is not None:
        _log.info("local admin %r already present, skipping", cfg.local_admin_username)
        return
    user = UserInternal(
        user_id=f"admin-{cfg.local_admin_username}",
        username=cfg.local_admin_username,
        tenant_id=_BOOTSTRAP_TENANT,
        roles=[RBACGlobalRole.ADMIN],
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        argon2id_hash=LocalMode.hash_password(cfg.local_admin_password),
    )
    await repo.insert_user(user)
    _log.info("bootstrapped local admin %r", cfg.local_admin_username)


async def _ensure_local_platform_manager(
    repo: AuthRepository, cfg: AuthConfig,
) -> None:
    """Create the bootstrap platform_manager (super-root) if
    `auth_mode == "local"` AND both `local_platform_manager_*` config
    fields are non-empty. Idempotent.

    Per E-100-002 v2 the platform_manager is **content-blind** —
    tenant lifecycle ops only (create/list/delete tenants), no
    access to projects / sources / conversations. Single-tenant
    deployments leave both fields empty and rely on admin alone.
    """
    if cfg.auth_mode != "local":
        return
    if not (
        cfg.local_platform_manager_username
        and cfg.local_platform_manager_password
    ):
        return
    existing = await repo.get_user_by_username(
        cfg.local_platform_manager_username,
    )
    if existing is not None:
        _log.info(
            "local platform_manager %r already present, skipping",
            cfg.local_platform_manager_username,
        )
        return
    user = UserInternal(
        user_id=f"tenant-manager-{cfg.local_platform_manager_username}",
        username=cfg.local_platform_manager_username,
        # platform_manager is cross-tenant by design — `tenant_id` is
        # decorative here. Same "default" tag the admin gets, for
        # symmetry.
        tenant_id="default",
        roles=[RBACGlobalRole.PLATFORM_MANAGER],
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        argon2id_hash=LocalMode.hash_password(
            cfg.local_platform_manager_password,
        ),
    )
    await repo.insert_user(user)
    _log.info(
        "bootstrapped local platform_manager %r",
        cfg.local_platform_manager_username,
    )


async def _ensure_demo_seed(
    repo: AuthRepository,
    cfg: AuthConfig,
    gitea: GiteaClient | None = None,
) -> None:
    """Pre-provision a complete manual-test scenario : 1 tenant +
    4 users (super-root / tenant-admin / project-editor /
    project-viewer) + 1 project + 2 project grants. Idempotent ;
    every step pre-checks existence before insert.

    Gated by `auth_mode == 'local'` AND `demo_seed_enabled`.
    PRODUCTION overlays SHALL leave `demo_seed_enabled` False —
    the demo accounts have well-known passwords by design.

    The companion flag `ux_dev_mode_enabled` controls whether the
    credentials are surfaced on `/ux/config`. The two flags are
    independent (defense-in-depth) : staging may seed without
    exposing ; the local stack overlay flips both to True.
    """
    if cfg.auth_mode != "local":
        return
    if not cfg.demo_seed_enabled:
        return

    now = datetime.now(UTC)
    tenant_id = cfg.demo_seed_tenant_id
    project_id = cfg.demo_seed_project_id

    # 1. Tenant — required parent for project + tenant-scoped users.
    if await repo.get_tenant(tenant_id) is None:
        await repo.insert_tenant(tenant_id, cfg.demo_seed_tenant_name, now)
        _log.info("demo seed: created tenant %r", tenant_id)

    # 2. Users (4) — pre-check by username. user_id is deterministic
    # so re-runs across restarts re-find the same record.
    users_to_seed: list[tuple[str, str, str, str, RBACGlobalRole]] = [
        # (username, password, user_id, user_tenant_id, role)
        # Super-root is cross-tenant by design — `tenant_id` is a
        # decorative tag, mirror admin/platform_manager bootstrap.
        (
            cfg.demo_seed_superroot_username,
            cfg.demo_seed_superroot_password,
            "demo-superroot",
            "default",
            RBACGlobalRole.PLATFORM_MANAGER,
        ),
        (
            cfg.demo_seed_tenant_admin_username,
            cfg.demo_seed_tenant_admin_password,
            "demo-tenant-admin",
            tenant_id,
            RBACGlobalRole.ADMIN,
        ),
        (
            cfg.demo_seed_project_owner_username,
            cfg.demo_seed_project_owner_password,
            "demo-project-owner",
            tenant_id,
            RBACGlobalRole.USER,
        ),
        (
            cfg.demo_seed_project_editor_username,
            cfg.demo_seed_project_editor_password,
            "demo-project-editor",
            tenant_id,
            RBACGlobalRole.USER,
        ),
        (
            cfg.demo_seed_project_viewer_username,
            cfg.demo_seed_project_viewer_password,
            "demo-project-viewer",
            tenant_id,
            RBACGlobalRole.USER,
        ),
    ]
    for username, password, user_id, user_tenant, role in users_to_seed:
        if await repo.get_user_by_username(username) is not None:
            continue
        user = UserInternal(
            user_id=user_id,
            username=username,
            tenant_id=user_tenant,
            roles=[role],
            status=UserStatus.ACTIVE,
            created_at=now,
            argon2id_hash=LocalMode.hash_password(password),
        )
        await repo.insert_user(user)
        _log.info(
            "demo seed: created user %r (id=%s, role=%s)",
            username, user_id, role.value,
        )

    # 3. Projects (2) — one CodeGen project (`project-test`, profile=code,
    # full pipeline UX with Pipeline / Validation / Requirements) and one
    # DocGen project (`project-docgen`, profile=docgen, simplified UX
    # with Conversations + Documents only). Both seeded under the same
    # tenant with identical grants. Per-project provisioning logic is
    # factored into `_seed_demo_project` so the two flows share the
    # idempotent Gitea + backfill scaffolding.
    await _seed_demo_project(
        repo,
        gitea=gitea,
        tenant_id=tenant_id,
        project_id=project_id,
        project_name=cfg.demo_seed_project_name,
        profile="code",
        now=now,
    )
    await _seed_demo_project(
        repo,
        gitea=gitea,
        tenant_id=tenant_id,
        project_id=cfg.demo_seed_docgen_project_id,
        project_name=cfg.demo_seed_docgen_project_name,
        profile="docgen",
        now=now,
    )

    # 4. Project grants on BOTH demo projects so the demo editor /
    # viewer credentials work on each one. `grant_project_role` uses
    # `overwrite=True` so re-running is safe.
    grants_to_seed: list[tuple[str, RBACProjectRole]] = [
        ("demo-project-owner", RBACProjectRole.OWNER),
        ("demo-project-editor", RBACProjectRole.EDITOR),
        ("demo-project-viewer", RBACProjectRole.VIEWER),
    ]
    for target_project in (project_id, cfg.demo_seed_docgen_project_id):
        for grantee_id, project_role in grants_to_seed:
            await repo.grant_project_role(
                grantee_id, target_project, project_role.value,
            )
            _log.info(
                "demo seed: granted %s on project %r to user %s",
                project_role.value, target_project, grantee_id,
            )


async def _seed_demo_project(
    repo: AuthRepository,
    *,
    gitea: GiteaClient | None,
    tenant_id: str,
    project_id: str,
    project_name: str,
    profile: str,
    now: datetime,
) -> None:
    """Idempotent provisioning of a single demo project. Handles three
    cases : (a) project missing → create + provision Gitea ; (b) project
    exists without git_repo_url → backfill Gitea ; (c) project fully
    provisioned → no-op. Shared between project-test (profile=code) and
    project-docgen (profile=docgen) so the two demo projects have the
    same shape (R-200-141 + R-200-142)."""
    existing = await repo.get_project(project_id)
    if existing is None:
        git_repo_url: str | None = None
        if gitea is not None:
            svc_user = f"svc-{tenant_id}-{project_id}"
            svc_pwd = uuid.uuid4().hex
            try:
                await gitea.create_user(
                    username=svc_user,
                    password=svc_pwd,
                    email=f"{svc_user}@aywizz.local",
                )
                repo_obj = await gitea.create_repo(
                    owner=svc_user,
                    name=project_id,
                    description=f"Backing repo for demo project {project_id!r}.",
                )
                await repo.upsert_project_secret(
                    project_id,
                    {
                        "gitea_username": svc_user,
                        "gitea_password": svc_pwd,
                        "gitea_repo_full_name": repo_obj.full_name,
                    },
                )
                git_repo_url = repo_obj.clone_url
                _log.info("demo seed: gitea repo %s ready", repo_obj.full_name)
            except Exception as exc:
                _log.warning(
                    "demo seed: gitea provisioning failed for %r (%s) — "
                    "project created without a backing repo",
                    project_id, exc,
                )
        await repo.insert_project(
            project_id,
            tenant_id,
            project_name,
            now,
            "demo-tenant-admin",
            profile=profile,
            git_repo_url=git_repo_url,
        )
        _log.info(
            "demo seed: created project %r in tenant %r (profile=%s)",
            project_id, tenant_id, profile,
        )
        return
    if gitea is not None and not existing.get("git_repo_url"):
        # Backfill : project pre-dates Gitea provisioning. Idempotent —
        # if `git_repo_url` is already set we don't reach this branch.
        svc_user = f"svc-{tenant_id}-{project_id}"
        svc_pwd = uuid.uuid4().hex
        try:
            await gitea.create_user(
                username=svc_user,
                password=svc_pwd,
                email=f"{svc_user}@aywizz.local",
            )
            repo_obj = await gitea.create_repo(
                owner=svc_user,
                name=project_id,
                description=f"Backing repo for demo project {project_id!r}.",
            )
            await repo.upsert_project_secret(
                project_id,
                {
                    "gitea_username": svc_user,
                    "gitea_password": svc_pwd,
                    "gitea_repo_full_name": repo_obj.full_name,
                },
            )
            await repo.update_project(
                project_id, {"git_repo_url": repo_obj.clone_url},
            )
            _log.info(
                "demo seed: backfilled gitea repo %s for existing project %r",
                repo_obj.full_name, project_id,
            )
        except Exception as exc:
            _log.warning(
                "demo seed: gitea backfill failed for %r (%s) — project "
                "keeps no git_repo_url",
                project_id, exc,
            )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    cfg = config or AuthConfig()
    log_cfg = LoggingSettings()
    configure_logging(component="c2_auth", settings=log_cfg)
    repo = AuthRepository.from_config(
        cfg.arango_url,
        cfg.arango_db,
        cfg.arango_username,
        cfg.arango_password,
    )
    # Gitea client : present when `C2_GITEA_BASE_URL` is non-empty.
    # Skipping it for fixture-style tests that don't have a Gitea
    # container is supported (the service falls back to legacy
    # "no git repo" project creation — R-200-142 says `git_repo_url`
    # MAY be None for backwards compat).
    gitea: GiteaClient | None = None
    if cfg.gitea_base_url:
        gitea = GiteaClient(
            base_url=cfg.gitea_base_url,
            admin_username=cfg.gitea_admin_username,
            admin_password=cfg.gitea_admin_password,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await repo.ensure_collections()
        # The tenant FIRST: both user bootstraps below mint identities into
        # it, and a user in a non-existent tenant is the half-built state
        # that took the system-tests workflow red.
        await _ensure_bootstrap_tenant(repo, cfg)
        await _ensure_local_admin(repo, cfg)
        await _ensure_local_platform_manager(repo, cfg)
        await _ensure_demo_seed(repo, cfg, gitea=gitea)
        yield
        if gitea is not None:
            await gitea.aclose()

    app = FastAPI(title="C2 Auth Service", lifespan=lifespan, **docs_urls("c2"))
    # TWO DIFFERENT FACTS, deliberately two lists. An earlier pass here
    # merged them and the document came out wrong.
    #
    # `guard_exempt` — paths that need no INJECTED IDENTITY HEADERS.
    # `unauthenticated` — paths that need no TOKEN AT ALL.
    #
    # `/auth/verify` is in the first and not the second: it is the
    # forward-auth endpoint, so it is reached before any identity has been
    # injected, and its whole job is to verify a bearer token. Documenting
    # it as needing no token would describe the opposite of what it does.
    # `/auth/login` and `/auth/token` take credentials rather than a token
    # and still answer 401 when those are wrong, so they declare their own
    # 401 with that meaning rather than inheriting "no verified identity".
    guard_exempt = (
        "/health",
        "/auth/config",
        "/auth/login",
        "/auth/token",
        "/auth/verify",
        "/ux/config",
    )
    unauthenticated = frozenset(
        {"/health", "/auth/config", "/auth/login", "/auth/token", "/ux/config"}
    )
    # AuthGuardMiddleware (innermost, runs after TraceContext) — C2's
    # public auth surface (login/token/verify/config + the UX
    # bootstrap config) is exempt; every other path requires
    # X-User-Id propagated by Traefik forward-auth.
    app.add_middleware(
        AuthGuardMiddleware,
        component="c2_auth",
        exempt_prefixes=list(guard_exempt),
    )
    app.add_middleware(TraceContextMiddleware, sample_rate=log_cfg.trace_sample_rate)
    app.include_router(router, prefix="/auth")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(projects_router, prefix="/api/v1/projects")
    app.include_router(preferences_router, prefix="/api/v1/users/me/preferences")
    app.include_router(ux_router, prefix="/ux")
    service = AuthService(cfg, repo, gitea=gitea)
    app.dependency_overrides[c2_get_service] = lambda: service

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe for the kubelet (R-100-114).

        Answers `ok` whenever the process serves requests, and
        deliberately checks NO dependency: a probe that fails because
        ArangoDB is slow takes the pod out of service for a condition
        restarting it cannot fix. Reachable without a token — the
        kubelet has none.
        """
        return {"status": "ok", "component": "c2_auth"}

    describe_app(
        app,
        summary="Authentication, tenants, projects and the role model.",
        description="""
C2 is the platform's identity authority. It issues the bearer token every
other component's requests are measured against, and it is the only
component that can verify one — which is why its own token surface is
reached without `forward-auth` (that would be circular) and authenticates
itself in-process instead.

### Getting a token

`POST /auth/login` with a username and password returns an `access_token`.
Send it as `Authorization: Bearer <token>` on every other call in this
document and in every other component's.

### The role model (`E-100-002`)

Global roles — `platform_manager`, `admin` (alias `tenant_admin`), `user` —
govern the PLATFORM: tenants, projects, members, quotas. Project roles —
`project_owner`, `project_editor`, `project_viewer` — govern a project's
CONTENT, and they are granted per project.

The two do not substitute for one another. `admin` and `tenant_admin` are
**content-blind** by design: they can create a project and manage its
members, and they cannot read its requirements, sources or conversations
without also holding a role on it. `platform_manager` is content-blind
across every tenant. That separation is the point of the model, not an
oversight — so expect a 403 on project content from an otherwise
all-powerful administrator.

### Routes reachable without a token

`POST /auth/login`, `POST /auth/token`, `GET /auth/config`,
`GET /ux/config`, `GET /health`. Everything else refuses with 401.

`POST /auth/login` and `POST /auth/token` take credentials instead of a
token, and answer 401 when they are wrong — a different 401 from the one
every other route means. `GET /auth/verify` is the gateway's own
forward-auth hop: it requires a bearer token like any other route, and is
documented here because an operator may need to call it directly when
diagnosing a 403.
""",
        public_paths=unauthenticated,
    )
    return app


app = create_app()
