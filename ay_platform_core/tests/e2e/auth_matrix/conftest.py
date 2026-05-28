# =============================================================================
# File: conftest.py
# Version: 1
# Path: ay_platform_core/tests/e2e/auth_matrix/conftest.py
# Description: Session-scoped fixtures for the auth x role x scope matrix.
#              Builds ONE platform stack per session (real ArangoDB +
#              MinIO via testcontainers) plus a pre-seeded set of two
#              tenants x two projects so isolation tests can validate
#              cross-tenant / cross-project leak.
#
#              Roles in the matrix follow E-100-002 v2:
#                tenant_manager  — super-root, content-blind
#                admin           — tenant-scoped admin
#                project_owner   — owner of a specific project
#                project_editor  — editor of a specific project
#                project_viewer  — read-only on a specific project
#                user            — authenticated, no grant
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from ay_platform_core.c2_auth.models import RBACGlobalRole, UserCreateRequest
from tests.e2e.auth_matrix._clients import RoleProfile
from tests.e2e.auth_matrix._stack import PlatformStack, build_stack
from tests.fixtures.containers import (
    ArangoEndpoint,
    MinioEndpoint,
    cleanup_arango_database,
    cleanup_minio_bucket,
)

# ---------------------------------------------------------------------------
# Stack fixture — session-scoped to amortise testcontainers cost
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def auth_matrix_stack(
    arango_container: ArangoEndpoint,
    minio_container: MinioEndpoint,
) -> AsyncIterator[PlatformStack]:
    """Build the full 7-component stack ONCE per pytest session.

    `loop_scope="session"` is REQUIRED — without it pytest-asyncio
    builds the fixture inside the first test's function-scoped event
    loop; subsequent tests see a stack whose loop has been closed and
    silently hang on the first awaited operation. This was the root
    cause of the matrix hang seen in the early auth_matrix iterations.

    Per-test isolation is achieved by giving each scenario its own
    project_id / source_id / etc. (factories) rather than rebuilding
    the stack — testcontainer startup is the dominant cost.
    """
    async with build_stack(
        arango_url=arango_container.url,
        arango_password=arango_container.password,
        minio_endpoint=minio_container.endpoint,
        minio_access=minio_container.access_key,
        minio_secret=minio_container.secret_key,
    ) as stack:
        try:
            yield stack
        finally:
            for bucket in stack.bucket_names.values():
                cleanup_minio_bucket(minio_container, bucket)
            for db_name in stack.db_names.values():
                cleanup_arango_database(arango_container, db_name)


# ---------------------------------------------------------------------------
# Identity profiles
#
# We pre-allocate one profile per (role, tenant) combination needed by
# the parametrized tests. `tenant_a` is the "subject" tenant (the one
# we attempt operations against); `tenant_b` is the foreign tenant used
# by isolation tests to verify cross-tenant leak. Same idea for
# project_a (the in-scope project) vs project_b (the cross-project leak
# target within tenant_a).
# ---------------------------------------------------------------------------


TENANT_A = "tenant-auth-a"
TENANT_B = "tenant-auth-b"
PROJECT_A = "project-auth-a"
PROJECT_B = "project-auth-b"


@pytest.fixture(scope="session")
def profiles() -> dict[str, RoleProfile]:
    """Pre-built RoleProfile per role label, all in tenant_a.

    Tests that need cross-tenant or cross-project variants use the
    `*_other_tenant` and `*_other_project` profiles.
    """
    return {
        "anonymous": RoleProfile(
            user_id="", tenant_id="", global_roles=(), project_id=None,
        ),
        "user": RoleProfile(
            user_id="u-user-a",
            tenant_id=TENANT_A,
            global_roles=("user",),
        ),
        "project_viewer": RoleProfile(
            user_id="u-pv-a",
            tenant_id=TENANT_A,
            project_id=PROJECT_A,
            project_role="project_viewer",
        ),
        "project_editor": RoleProfile(
            user_id="u-pe-a",
            tenant_id=TENANT_A,
            project_id=PROJECT_A,
            project_role="project_editor",
        ),
        "project_owner": RoleProfile(
            user_id="u-po-a",
            tenant_id=TENANT_A,
            project_id=PROJECT_A,
            project_role="project_owner",
        ),
        "admin": RoleProfile(
            user_id="u-adm-a",
            tenant_id=TENANT_A,
            global_roles=("admin",),
            project_id=PROJECT_A,
        ),
        "tenant_admin": RoleProfile(
            user_id="u-tadm-a",
            tenant_id=TENANT_A,
            global_roles=("tenant_admin",),
            project_id=PROJECT_A,
        ),
        "tenant_manager": RoleProfile(
            user_id="u-tmgr",
            # tenant_manager is platform-wide; the X-Tenant-Id of the
            # request is whatever resource they target. By convention
            # we set TENANT_A so tenant_manager calls don't 401 on
            # missing tenant header.
            tenant_id=TENANT_A,
            global_roles=("tenant_manager",),
        ),
        # Cross-tenant probe: same role suite as project_owner, but in
        # tenant B. Used to assert that a cross-tenant attempt against
        # tenant A's resources returns 403/404 (no leak).
        "project_owner_other_tenant": RoleProfile(
            user_id="u-po-b",
            tenant_id=TENANT_B,
            project_id=PROJECT_A,  # NB: targets A's project — the
            # discriminator is the tenant header, not the path.
            project_role="project_owner",
        ),
        # Cross-project probe in tenant A: profile lacks any project
        # role on PROJECT_A; carries a role on PROJECT_B instead.
        "project_owner_other_project": RoleProfile(
            user_id="u-po-other-proj",
            tenant_id=TENANT_A,
            project_id=PROJECT_B,
            project_role="project_owner",
        ),
    }


# ---------------------------------------------------------------------------
# Test users in C2 (so /auth/login flow works for auth_modes tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def seeded_c2_users(auth_matrix_stack: PlatformStack) -> dict[str, str]:
    """Create one user account per role in C2 so the /auth/login flow
    is exercisable. Returns a mapping role → password."""
    service = auth_matrix_stack.c2_service
    creds: dict[str, str] = {}

    seedlings = [
        ("admin", [RBACGlobalRole.ADMIN]),
        ("tenant_admin", [RBACGlobalRole.TENANT_ADMIN]),
        ("tenant_manager", [RBACGlobalRole.TENANT_MANAGER]),
        ("user", [RBACGlobalRole.USER]),
    ]
    for role_label, roles in seedlings:
        username = f"{role_label}-{uuid.uuid4().hex[:6]}@auth-matrix.test"
        password = f"pw-{role_label}-strong!"
        await service.create_user(
            UserCreateRequest(
                username=username,
                password=password,
                tenant_id=TENANT_A,
                roles=roles,
                name=f"auth-matrix {role_label}",
                email=username,
            )
        )
        creds[role_label] = f"{username}::{password}"
    return creds
