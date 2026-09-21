# =============================================================================
# File: test_service_admin_guards.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_service_admin_guards.py
# Description: Unit tests for AuthService's administrative guard branches —
#              the decisions that were reachable in production but had never
#              been taken by any test (2026-09-20 branch audit).
#
#              These are grouped by what they PROTECT, not by method:
#                - cross-tenant isolation on the reverse ACL view ;
#                - user state transitions (status / roles / profile patch) ;
#                - referential 404s and 409s on project governance ;
#                - the Gitea compensating rollback that keeps a failed
#                  project creation from leaving an orphan repo.
#
#              None of them are defensive dead ends: each one decides
#              something an operator or a tenant boundary depends on.
#
# @relation validates:E-100-002
# @relation validates:R-200-141
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.config import AuthConfig
from ay_platform_core.c2_auth.models import (
    ProjectCreate,
    RBACGlobalRole,
    RBACProjectRole,
    UserInternal,
    UserStatus,
    UserUpdateRequest,
)
from ay_platform_core.c2_auth.service import AuthService

pytestmark = pytest.mark.unit

_NOW = datetime.now(UTC)


def _user(user_id: str = "u-1", tenant_id: str = "t-1") -> UserInternal:
    return UserInternal(
        user_id=user_id,
        username=f"user-{user_id}",
        tenant_id=tenant_id,
        roles=[RBACGlobalRole.USER],
        status=UserStatus.ACTIVE,
        created_at=_NOW,
        argon2id_hash="x" * 32,
    )


def _service(repo: AsyncMock, gitea: object | None = None) -> AuthService:
    config = AuthConfig.model_validate({
        "auth_mode": "local",
        "jwt_secret_key": "test-secret-key-32-chars-minimum!",
        "platform_environment": "testing",
    })
    return AuthService(config, repo, gitea=gitea)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cross-tenant isolation on the reverse ACL view
# ---------------------------------------------------------------------------


async def test_tenant_scoped_listing_drops_another_tenants_project() -> None:
    """A tenant operator SHALL NOT see a user's grants in a foreign tenant.

    `list_user_project_access(tenant_scope=...)` filters each grant by the
    OWNING project's tenant. The service's own docstring calls this "defence
    in depth — a user's grants are same-tenant by construction, but we filter
    regardless"; that filter had never actually dropped anything in a test,
    so the defence was asserted nowhere. A grant that crossed the boundary
    (through a bug elsewhere, which is the whole point of defence in depth)
    would have leaked a foreign project's id and NAME to the operator.
    """
    repo = AsyncMock()
    repo.get_project_scopes.return_value = {
        "p-home": [RBACProjectRole.OWNER.value],
        "p-foreign": [RBACProjectRole.VIEWER.value],
    }

    async def _get_project(project_id: str) -> dict[str, str]:
        return {
            "p-home": {"tenant_id": "t-1", "name": "Home"},
            "p-foreign": {"tenant_id": "t-OTHER", "name": "Someone Else"},
        }[project_id]

    repo.get_project.side_effect = _get_project

    listing = await _service(repo).list_user_project_access("u-1", tenant_scope="t-1")

    assert [item.project_id for item in listing.items] == ["p-home"]
    # The foreign project's NAME must not surface either — the filter runs
    # before the display join, and a regression that filtered late would
    # still leak it here.
    assert all(item.project_name != "Someone Else" for item in listing.items)


async def test_unscoped_listing_returns_every_tenant() -> None:
    """The platform operator (`tenant_scope=None`) SHALL see everything.

    Pins the other side of the same `if`: a fix that filtered unconditionally
    would silently blind the cross-tenant operator role.
    """
    repo = AsyncMock()
    repo.get_project_scopes.return_value = {
        "p-home": [RBACProjectRole.OWNER.value],
        "p-foreign": [RBACProjectRole.VIEWER.value],
    }
    repo.get_project.side_effect = lambda pid: {
        "p-home": {"tenant_id": "t-1", "name": "Home"},
        "p-foreign": {"tenant_id": "t-OTHER", "name": "Someone Else"},
    }[pid]

    listing = await _service(repo).list_user_project_access("u-1")

    assert {item.project_id for item in listing.items} == {"p-home", "p-foreign"}


# ---------------------------------------------------------------------------
# User state transitions
# ---------------------------------------------------------------------------


async def test_update_user_patches_status_roles_name_and_email() -> None:
    """Every optional field SHALL reach the repository when supplied.

    `status` is the security-relevant one: it is how an operator moves a
    user to DISABLED. Each field sits behind its own `if ... is not None`,
    and only `roles` had ever been exercised — a patch that silently dropped
    `status` would leave a revoked account able to log in.
    """
    repo = AsyncMock()
    repo.get_user_by_id.return_value = _user()

    await _service(repo).update_user(
        "u-1",
        UserUpdateRequest(
            roles=[RBACGlobalRole.ADMIN],
            status=UserStatus.DISABLED,
            name="New Name",
            email="new@example.test",
        ),
    )

    repo.update_user.assert_awaited_once_with(
        "u-1",
        {
            "roles": [RBACGlobalRole.ADMIN.value],
            "status": UserStatus.DISABLED.value,
            "name": "New Name",
            "email": "new@example.test",
        },
    )


async def test_update_user_with_an_empty_patch_does_not_write() -> None:
    """An all-`None` request SHALL NOT reach the repository at all.

    Guards the `if patch:` branch: writing an empty patch would bump the
    document (and any `last_modified` trigger) for a request that asked for
    no change.
    """
    repo = AsyncMock()
    repo.get_user_by_id.return_value = _user()

    await _service(repo).update_user("u-1", UserUpdateRequest())

    repo.update_user.assert_not_awaited()


async def test_update_user_unknown_id_is_404() -> None:
    repo = AsyncMock()
    repo.get_user_by_id.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await _service(repo).update_user(
            "ghost", UserUpdateRequest(name="irrelevant")
        )
    assert exc_info.value.status_code == 404
    repo.update_user.assert_not_awaited()


async def test_update_user_vanishing_mid_flight_is_404_not_a_crash() -> None:
    """The post-write re-read SHALL 404 rather than raise on `None`.

    The user exists at the guard and is gone by the re-read — a concurrent
    delete. Without this branch the service would blow up on
    `None.model_dump()` and answer 500 to what is a legitimate 404.
    """
    repo = AsyncMock()
    repo.get_user_by_id.side_effect = [_user(), None]

    with pytest.raises(HTTPException) as exc_info:
        await _service(repo).update_user("u-1", UserUpdateRequest(name="Gone"))
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Project governance: referential guards
# ---------------------------------------------------------------------------


async def test_grant_access_to_unknown_project_is_404() -> None:
    repo = AsyncMock()
    repo.get_project.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await _service(repo).grant_project_access(
            "ghost-project", "u-1", RBACProjectRole.VIEWER, actor_id="op"
        )
    assert exc_info.value.status_code == 404
    repo.grant_project_role.assert_not_awaited()


async def test_grant_access_to_unknown_user_is_404() -> None:
    """A grant to a non-existent user SHALL NOT be written.

    Distinct from the project guard above: this one fires AFTER the project
    check, so a regression that reordered or dropped it would create a
    dangling ACL row pointing at no user.
    """
    repo = AsyncMock()
    repo.get_project.return_value = {"tenant_id": "t-1", "name": "P"}
    repo.get_user_by_id.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await _service(repo).grant_project_access(
            "p-1", "ghost-user", RBACProjectRole.VIEWER, actor_id="op"
        )
    assert exc_info.value.status_code == 404
    repo.grant_project_role.assert_not_awaited()


async def test_create_project_in_unknown_tenant_is_404() -> None:
    """Projects SHALL NOT dangle in a tenant that does not exist."""
    repo = AsyncMock()
    repo.get_tenant.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await _service(repo).create_project(
            ProjectCreate(project_id="p-1", name="P"), "t-ghost", actor_id="op"
        )
    assert exc_info.value.status_code == 404
    repo.insert_project.assert_not_awaited()


async def test_create_duplicate_project_is_409() -> None:
    repo = AsyncMock()
    repo.get_tenant.return_value = {"tenant_id": "t-1", "active": True}
    repo.get_project.return_value = {"tenant_id": "t-1", "name": "Existing"}

    with pytest.raises(HTTPException) as exc_info:
        await _service(repo).create_project(
            ProjectCreate(project_id="p-1", name="P"), "t-1", actor_id="op"
        )
    assert exc_info.value.status_code == 409
    repo.insert_project.assert_not_awaited()


# ---------------------------------------------------------------------------
# The Gitea compensating rollback (R-200-141)
# ---------------------------------------------------------------------------


async def test_failed_insert_rolls_the_gitea_account_back() -> None:
    """A DB failure AFTER Gitea succeeded SHALL tear the Gitea side down.

    `create_project` provisions Gitea FIRST so a provisioning failure leaves
    no Arango row. That ordering creates the opposite hazard, which this
    branch exists to close: when the Arango insert fails, the Gitea service
    account and its repo already exist. Leaving them behind is not cosmetic —
    the code's own comment says the next retry would then 409 on the user
    creation, so a single transient DB error would make the project
    PERMANENTLY uncreatable until an operator cleaned Gitea by hand.

    Both branches of `if self._gitea is not None` inside the `except` were at
    0% coverage: the compensating transaction had never run.
    """
    repo = AsyncMock()
    repo.get_tenant.return_value = {"tenant_id": "t-1", "active": True}
    repo.get_project.return_value = None
    repo.insert_project.side_effect = RuntimeError("arango is down")

    gitea = AsyncMock()
    gitea.create_repo.return_value = SimpleNamespace(
        full_name="svc-t-1-p-1/p-1",
        clone_url="http://gitea:3000/svc-t-1-p-1/p-1.git",
    )

    service = _service(repo, gitea=gitea)

    # The original error SHALL propagate — the rollback must not swallow it,
    # or the caller would see a success for a project that does not exist.
    with pytest.raises(RuntimeError, match="arango is down"):
        await service.create_project(
            ProjectCreate(project_id="p-1", name="P"), "t-1", actor_id="op"
        )

    # The service account created moments earlier is deleted, with `purge`
    # so the repo underneath goes with it.
    gitea.delete_user.assert_awaited_once()
    # And the stored credentials go too, or the next attempt would resurrect
    # a secret pointing at a repo that no longer exists.
    repo.delete_project_secret.assert_awaited_once_with("p-1")


async def test_failed_insert_without_gitea_just_propagates() -> None:
    """With no Gitea configured there is nothing to compensate.

    The other side of the same `if`: a rollback attempted on a `None` client
    would turn a clean DB error into an AttributeError, masking the real
    cause behind a crash in the error handler.
    """
    repo = AsyncMock()
    repo.get_tenant.return_value = {"tenant_id": "t-1", "active": True}
    repo.get_project.return_value = None
    repo.insert_project.side_effect = RuntimeError("arango is down")

    with pytest.raises(RuntimeError, match="arango is down"):
        await _service(repo).create_project(
            ProjectCreate(project_id="p-1", name="P"), "t-1", actor_id="op"
        )
    repo.delete_project_secret.assert_not_awaited()
