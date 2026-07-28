# =============================================================================
# File: test_preferences_guards.py
# Version: 1
# Path: ay_platform_core/tests/unit/c2_auth/test_preferences_guards.py
# Description: Unit tests for the C2 preferences-router auth guards: the caller
#              identity is mandatory (401 without the forward-auth X-User-Id),
#              and a content-blind platform_manager is refused (403) — a
#              tenant-content surface (E-100-002 v2). Direct dependency-function
#              tests; no HTTP stack needed.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import pytest
from fastapi import HTTPException

from ay_platform_core.c2_auth.preferences_router import (
    _reject_platform_manager,
    _require_actor,
)

pytestmark = pytest.mark.unit


def test_require_actor_returns_the_user_id() -> None:
    assert _require_actor("u-42") == "u-42"


@pytest.mark.parametrize("missing", [None, ""])
def test_require_actor_401_without_forward_auth(missing: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_actor(missing)
    assert exc.value.status_code == 401


def test_reject_platform_manager_403_for_pure_operator() -> None:
    with pytest.raises(HTTPException) as exc:
        _reject_platform_manager("platform_manager")
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "roles",
    [
        None,  # anonymous / no roles
        "admin",  # a tenant admin — content access is legitimate
        "tenant_admin",
        "platform_manager,admin",  # hybrid: the content role admits it
        "project_editor",
    ],
)
def test_reject_platform_manager_allows_everyone_else(roles: str | None) -> None:
    # Must NOT raise — only a PURE platform_manager (no content role) is refused.
    _reject_platform_manager(roles)
