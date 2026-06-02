# =============================================================================
# File: test_verify_project_role_forward_auth.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_verify_project_role_forward_auth.py
# Description: Regression tests for C2 forward-auth PROJECT-ROLE propagation
#              (E-100-002). `/auth/verify` SHALL emit, in `X-User-Roles`, the
#              caller's project-scoped role for the project named in the
#              forwarded request URI (`X-Forwarded-Uri`) — so a project_editor
#              / project_owner can act on EVERY project they hold a scope on,
#              and ONLY those. Before the fix, forward-auth emitted only the
#              GLOBAL roles, making every project-scoped gate (e.g. C7
#              `POST /sources/upload`, which requires project_editor/owner/
#              admin) effectively global-admin-only — a project_editor got a
#              spurious 403. The auth-matrix tests missed this because they
#              INJECT `X-User-Roles` directly instead of exercising the real
#              `/verify` forward-auth, so they never saw the global-only
#              emission. This test exercises the real endpoint.
# =============================================================================

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ay_platform_core.c2_auth.models import JWTClaims, RBACGlobalRole, RBACProjectRole
from ay_platform_core.c2_auth.router import (
    _get_current_claims,
    _project_id_from_uri,
    router,
)

pytestmark = pytest.mark.unit

# The roles C7's upload gate accepts (R-100-081 v3 / E-100-002).
UPLOAD_ROLES = {"project_editor", "project_owner", "admin"}
# The roles the source DELETE gate accepts — STRICTER than upload: the
# project_owner owns (and deletes) their project's documents; an editor adds
# but does not delete. E-100-002.
DELETE_ROLES = {"project_owner", "admin"}


def _claims(
    *,
    roles: list[RBACGlobalRole] | None = None,
    project_scopes: dict[str, list[RBACProjectRole]] | None = None,
) -> JWTClaims:
    return JWTClaims(
        sub="user-1",
        iat=1_700_000_000,
        exp=1_700_003_600,
        jti="jti-1",
        auth_mode="none",
        tenant_id="tenant-test",
        roles=roles or [],
        project_scopes=project_scopes or {},
    )


def _emitted_roles(claims: JWTClaims, forwarded_uri: str) -> set[str]:
    """GET /auth/verify with the given claims + forwarded URI; return the
    set of roles C2 emits in `X-User-Roles`."""
    app = FastAPI()
    app.include_router(router, prefix="/auth")  # matches the production mount
    app.dependency_overrides[_get_current_claims] = lambda: claims
    client = TestClient(app)
    resp = client.get("/auth/verify", headers={"X-Forwarded-Uri": forwarded_uri})
    assert resp.status_code == 200, resp.text
    raw = resp.headers.get("X-User-Roles", "")
    return {r for r in raw.split(",") if r}


def _can_upload(claims: JWTClaims, project_id: str) -> bool:
    uri = f"/api/v1/memory/projects/{project_id}/sources/upload"
    return bool(_emitted_roles(claims, uri) & UPLOAD_ROLES)


def _can_delete(claims: JWTClaims, project_id: str) -> bool:
    uri = f"/api/v1/memory/projects/{project_id}/sources/src-1"
    return bool(_emitted_roles(claims, uri) & DELETE_ROLES)


# ---------------------------------------------------------------------------
# Pure URI parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("/api/v1/memory/projects/project-docgen/sources/upload", "project-docgen"),
        ("/api/v1/projects/p2/requirements/documents", "p2"),
        ("/api/v1/admin/projects/p3/artifacts/seed", "p3"),
        ("/api/v1/memory/projects/p%2Fweird/sources", "p/weird"),  # url-decoded
        ("/api/v1/projects", None),  # list — no {id}
        ("/api/v1/conversations", None),
        ("", None),
    ],
)
def test_project_id_from_uri(uri: str, expected: str | None) -> None:
    assert _project_id_from_uri(uri) == expected


# ---------------------------------------------------------------------------
# Project-role propagation — the upload matrix (who CAN, who CANNOT)
# ---------------------------------------------------------------------------


def test_project_editor_can_upload_to_their_project() -> None:
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.EDITOR]})
    assert _can_upload(claims, "project-docgen") is True
    assert "project_editor" in _emitted_roles(
        claims, "/api/v1/memory/projects/project-docgen/sources/upload"
    )


def test_project_owner_can_upload_to_their_project() -> None:
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.OWNER]})
    assert _can_upload(claims, "project-docgen") is True


def test_global_admin_can_upload_to_any_project() -> None:
    claims = _claims(roles=[RBACGlobalRole.ADMIN])  # no project scopes
    assert _can_upload(claims, "project-docgen") is True
    assert _can_upload(claims, "any-other-project") is True


def test_editor_on_multiple_projects_can_upload_to_each() -> None:
    claims = _claims(
        project_scopes={
            "project-docgen": [RBACProjectRole.EDITOR],
            "project-test": [RBACProjectRole.OWNER],
        }
    )
    assert _can_upload(claims, "project-docgen") is True
    assert _can_upload(claims, "project-test") is True


def test_project_viewer_cannot_upload() -> None:
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.VIEWER]})
    assert _can_upload(claims, "project-docgen") is False
    # ...but the viewer role IS propagated (so read gates work).
    assert "project_viewer" in _emitted_roles(
        claims, "/api/v1/memory/projects/project-docgen/sources/upload"
    )


def test_editor_cannot_upload_to_a_different_project_no_leak() -> None:
    """Cross-project isolation: editor on project-docgen must NOT be able to
    upload to project-test (a project they hold no scope on)."""
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.EDITOR]})
    assert _can_upload(claims, "project-test") is False
    assert "project_editor" not in _emitted_roles(
        claims, "/api/v1/memory/projects/project-test/sources/upload"
    )


def test_no_role_user_cannot_upload() -> None:
    claims = _claims(roles=[RBACGlobalRole.USER])  # baseline, no scopes
    assert _can_upload(claims, "project-docgen") is False


def test_tenant_manager_cannot_upload_content() -> None:
    """tenant_manager is content-blind (E-100-002): a global role that is NOT
    in the upload gate, with no project scopes."""
    claims = _claims(roles=[RBACGlobalRole.TENANT_MANAGER])
    assert _can_upload(claims, "project-docgen") is False


def test_global_roles_always_emitted_even_off_project_paths() -> None:
    claims = _claims(
        roles=[RBACGlobalRole.ADMIN],
        project_scopes={"project-docgen": [RBACProjectRole.EDITOR]},
    )
    # Non-project path: only global roles, no project role leaked.
    roles = _emitted_roles(claims, "/api/v1/conversations")
    assert roles == {"admin"}


# ---------------------------------------------------------------------------
# Source DELETE matrix — owner deletes THEIR project's docs, not others'
# (operator: "le project owner est propriétaire de SES documents mais il ne
# peut pas supprimer ceux des autres projets"). E-100-002.
# ---------------------------------------------------------------------------


def test_project_owner_can_delete_in_their_own_project() -> None:
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.OWNER]})
    assert _can_delete(claims, "project-docgen") is True


def test_project_owner_cannot_delete_in_another_project_no_leak() -> None:
    """The owner of project-docgen must NOT be able to delete a source of
    project-test (another project / common-to-others, owned elsewhere)."""
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.OWNER]})
    assert _can_delete(claims, "project-test") is False


def test_project_editor_cannot_delete_only_owner_can() -> None:
    """Editors ADD content; deletion is reserved to the owner (stricter gate)."""
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.EDITOR]})
    assert _can_upload(claims, "project-docgen") is True  # can add
    assert _can_delete(claims, "project-docgen") is False  # cannot delete


def test_project_viewer_cannot_delete() -> None:
    claims = _claims(project_scopes={"project-docgen": [RBACProjectRole.VIEWER]})
    assert _can_delete(claims, "project-docgen") is False


def test_global_admin_can_delete_anywhere() -> None:
    claims = _claims(roles=[RBACGlobalRole.ADMIN])
    assert _can_delete(claims, "project-docgen") is True
    assert _can_delete(claims, "any-project") is True


def test_owner_of_one_project_is_only_owner_there() -> None:
    """Multi-project: owner on A + editor on B → can delete on A, not on B."""
    claims = _claims(
        project_scopes={
            "project-a": [RBACProjectRole.OWNER],
            "project-b": [RBACProjectRole.EDITOR],
        }
    )
    assert _can_delete(claims, "project-a") is True
    assert _can_delete(claims, "project-b") is False  # editor there, not owner
