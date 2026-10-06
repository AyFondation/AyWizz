# =============================================================================
# File: test_verify_project_role_forward_auth.py
# Version: 2
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

import httpx2
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

# The roles C7's upload gate EFFECTIVELY accepts (R-100-081 v3 / E-100-002).
#
# `"admin"` is deliberately NOT here, and used to be. C7's route still lists
# it in `required=("project_editor", "project_owner", "admin")`, but
# `_require_role` subtracts `_CONTENT_BLIND_GLOBAL_ROLES = {"admin",
# "tenant_admin"}` from the caller's roles FIRST (E-100-002 v7), so `admin`
# can never satisfy that gate. Keeping it in this constant made the two
# "global admin can do anything" tests below assert a behaviour that cannot
# occur in production — the same stale `"admin"` dead text that was removed
# from C6's run-trigger tuple on 2026-10-05.
UPLOAD_ROLES = {"project_editor", "project_owner"}
# The roles the source DELETE gate effectively accepts — STRICTER than
# upload: the project_owner owns (and deletes) their project's documents; an
# editor adds but does not delete. E-100-002.
DELETE_ROLES = {"project_owner"}


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


def _verify(claims: JWTClaims, forwarded_uri: str) -> httpx2.Response:
    """GET /auth/verify with the given claims + forwarded URI.

    Returns the raw response rather than asserting 200: since E-100-002 v8
    `/verify` REFUSES a project-content request from a caller holding no
    grant on that project, so the status is part of the answer. The previous
    helper asserted 200 unconditionally, which modelled the boundary as
    "always admits, decision happens downstream" — true before v8, and the
    reason six tests here broke the moment the boundary started deciding.
    """
    app = FastAPI()
    app.include_router(router, prefix="/auth")  # matches the production mount
    app.dependency_overrides[_get_current_claims] = lambda: claims
    client = TestClient(app)
    return client.get("/auth/verify", headers={"X-Forwarded-Uri": forwarded_uri})


def _emitted_roles(claims: JWTClaims, forwarded_uri: str) -> set[str]:
    """The roles C2 emits in `X-User-Roles`. Requires the call to be admitted."""
    resp = _verify(claims, forwarded_uri)
    assert resp.status_code == 200, resp.text
    raw = resp.headers.get("X-User-Roles", "")
    return {r for r in raw.split(",") if r}


def _reaches_gate_with(claims: JWTClaims, uri: str, accepted: set[str]) -> bool:
    """Would this caller satisfy a content gate expecting `accepted`?

    Two things have to hold, and both are now real decisions:
      1. the BOUNDARY admits the request — `/verify` returns 200;
      2. the roles it emits, once the content-blind global roles are
         stripped the way every in-app `_require_role` strips them,
         intersect the gate's accepted set.

    Modelling step 2 without the stripping is what let `admin` look like a
    valid content role here for as long as it did.
    """
    resp = _verify(claims, uri)
    if resp.status_code != 200:
        return False
    raw = resp.headers.get("X-User-Roles", "")
    emitted = {r for r in raw.split(",") if r}
    emitted -= {"admin", "tenant_admin"}  # E-100-002 v7, as the gates do
    return bool(emitted & accepted)


def _can_upload(claims: JWTClaims, project_id: str) -> bool:
    uri = f"/api/v1/memory/projects/{project_id}/sources/upload"
    return _reaches_gate_with(claims, uri, UPLOAD_ROLES)


def _can_delete(claims: JWTClaims, project_id: str) -> bool:
    uri = f"/api/v1/memory/projects/{project_id}/sources/src-1"
    return _reaches_gate_with(claims, uri, DELETE_ROLES)


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


def test_global_admin_cannot_upload_without_a_project_grant() -> None:
    """A global `admin` holding no project scope SHALL NOT write content.

    **THIS ASSERTION IS INVERTED FROM ITS ORIGINAL.** It used to be
    `test_global_admin_can_upload_to_any_project`, asserting True on both
    lines — a behaviour that has been impossible since E-100-002 v7 made
    `admin` content-blind: every in-app `_require_role` subtracts it before
    testing the gate. The test passed only because its `UPLOAD_ROLES`
    constant still contained `"admin"` and it compared against the EMITTED
    header rather than against what a gate does with it.

    So this is not a tightening — it is the test catching up with a contract
    change made in v7. An admin who needs to write a project's content
    grants themselves a project role first, which is auditable; silent
    platform-wide write access was the thing v7 removed.
    """
    claims = _claims(roles=[RBACGlobalRole.ADMIN])  # no project scopes
    assert _can_upload(claims, "project-docgen") is False
    assert _can_upload(claims, "any-other-project") is False
    # Refused at the boundary now, not merely unsatisfied downstream.
    resp = _verify(claims, "/api/v1/memory/projects/project-docgen/sources/upload")
    assert resp.status_code == 403, resp.text


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
    # Since v8 the refusal happens AT the boundary, so there are no emitted
    # roles to inspect — which is a stronger guarantee than "the role was
    # not propagated": the request never reaches C7 at all.
    resp = _verify(claims, "/api/v1/memory/projects/project-test/sources/upload")
    assert resp.status_code == 403, resp.text
    assert "project_editor" not in resp.headers.get("X-User-Roles", "")


def test_no_role_user_cannot_upload() -> None:
    claims = _claims(roles=[RBACGlobalRole.USER])  # baseline, no scopes
    assert _can_upload(claims, "project-docgen") is False


def test_platform_manager_cannot_upload_content() -> None:
    """platform_manager is content-blind (E-100-002): a global role that is NOT
    in the upload gate, with no project scopes."""
    claims = _claims(roles=[RBACGlobalRole.PLATFORM_MANAGER])
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


def test_global_admin_cannot_delete_without_a_project_grant() -> None:
    """The delete counterpart of the inverted upload assertion.

    Was `test_global_admin_can_delete_anywhere`. Same reason: `admin` is
    content-blind since E-100-002 v7, and deleting a project's sources is
    content. Kept as its own test rather than folded into the upload one
    because DELETE is the more consequential half — a stale "admin can
    delete anywhere" is the assertion most likely to be cited as if it
    documented intended behaviour.
    """
    claims = _claims(roles=[RBACGlobalRole.ADMIN])
    assert _can_delete(claims, "project-docgen") is False
    assert _can_delete(claims, "any-project") is False


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


# ---------------------------------------------------------------------------
# E-100-002 v8 — the gateway project-role boundary
#
# One check at `/auth/verify` replaced what would otherwise have been a role
# gate added to 69 routes one at a time. These tests pin BOTH halves: what it
# refuses, and — just as important — what it must keep letting through, since
# a boundary that over-refuses breaks the operator surfaces that exist to
# recover a project.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "/api/v1/projects/p1/requirements/entities",
        "/api/v1/projects/p1/requirements/entities/R-1/history",
        "/api/v1/projects/p1/plans",
        "/api/v1/projects/p1/containers/030-ARCH/objects",
        "/api/v1/projects/p1/documents",
        "/api/v1/projects/p1/documents/a/b.md",
        "/api/v1/projects/p1/source/tree",
        "/api/v1/memory/projects/p1/sources",
        "/api/v1/memory/projects/p1/sources/s1/blob",
        "/api/v1/memory/projects/p1/kg/summary",
    ],
)
def test_content_uri_without_a_grant_is_refused(uri: str) -> None:
    """The 70-endpoint hole, closed at one point.

    Every URI here belonged to a route catalogued `AUTHENTICATED` +
    `Scope.PROJECT` — a project scope promised with nothing enforcing it.
    A caller authenticated with no grant on `p1` is now refused before the
    request leaves the gateway.
    """
    claims = _claims(roles=[RBACGlobalRole.USER])
    resp = _verify(claims, uri)
    assert resp.status_code == 403, f"{uri} admitted a caller with no grant"
    assert "project role" in resp.json()["detail"]


@pytest.mark.parametrize(
    "uri",
    [
        # Governance: the project's own metadata — an owner must still be
        # able to read and fix it.
        "/api/v1/projects/p1",
        # Its ACL: refusing here would make a lost grant unrecoverable.
        "/api/v1/projects/p1/members",
        "/api/v1/projects/p1/members/u1",
        # Platform-operator surface.
        "/api/v1/admin/projects/p1/artifacts/seed",
        # D-022 operator surface: backups accept admin / tenant_admin /
        # platform_manager BY DESIGN, with no project grant. These five
        # endpoints are the only project-scoped rows in the catalogue with a
        # non-empty `accept_global_roles`, which is why this is the single
        # sub-resource exception.
        "/api/v1/projects/p1/backups",
        "/api/v1/projects/p1/backups/b1/download",
        # Not project-scoped at all.
        "/api/v1/conversations",
        "/api/v1/projects",
    ],
)
def test_non_content_uri_is_not_subject_to_the_boundary(uri: str) -> None:
    """What the boundary SHALL NOT refuse.

    Without this half, the previous test would be satisfied by a boundary
    that refuses everything — which would lock operators out of the
    governance surfaces that exist to repair access, and break backup and
    restore.
    """
    claims = _claims(roles=[RBACGlobalRole.USER])
    resp = _verify(claims, uri)
    assert resp.status_code == 200, f"{uri} was refused but is not content"


def test_a_grant_on_the_right_project_admits_the_request() -> None:
    """The discriminating case: same URI, same caller, one grant added."""
    uri = "/api/v1/projects/p1/plans"
    assert _verify(_claims(roles=[RBACGlobalRole.USER]), uri).status_code == 403
    granted = _claims(project_scopes={"p1": [RBACProjectRole.VIEWER]})
    assert _verify(granted, uri).status_code == 200


def test_a_grant_on_another_project_does_not_admit_the_request() -> None:
    """Holding a scope SOMEWHERE is not holding it HERE.

    The confused-deputy case for the boundary: the check reads the scope map
    keyed by the project in the URI, never "does this caller hold any scope".
    """
    claims = _claims(project_scopes={"p-other": [RBACProjectRole.OWNER]})
    resp = _verify(claims, "/api/v1/projects/p1/plans")
    assert resp.status_code == 403


def test_an_empty_role_list_on_the_right_project_is_not_a_grant() -> None:
    """`{"p1": []}` SHALL NOT admit.

    A truthiness check on `claims.project_scopes.get(pid)` is what makes this
    correct; `pid in claims.project_scopes` would have admitted it. The
    distinction matters because a revoked grant can leave an empty list.
    """
    claims = _claims(project_scopes={"p1": []})
    resp = _verify(claims, "/api/v1/projects/p1/plans")
    assert resp.status_code == 403
