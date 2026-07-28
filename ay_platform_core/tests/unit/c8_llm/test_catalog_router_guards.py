# =============================================================================
# File: test_catalog_router_guards.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_catalog_router_guards.py
# Description: Unit tests for the C8 tenant-catalog router auth guards: the
#              tenant is mandatory (401 without the forward-auth X-Tenant-Id).
#              Direct dependency-function tests; no HTTP stack needed.
# =============================================================================

from __future__ import annotations

import pytest
from fastapi import HTTPException

from ay_platform_core.c8_llm.registry.catalog_router import _require_tenant

pytestmark = pytest.mark.unit


def test_require_tenant_returns_the_tenant_id() -> None:
    assert _require_tenant("t-acme") == "t-acme"


@pytest.mark.parametrize("missing", [None, ""])
def test_require_tenant_401_without_forward_auth(missing: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_tenant(missing)
    assert exc.value.status_code == 401
