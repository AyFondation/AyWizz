# =============================================================================
# File: test_route_catalog.py
# Version: 1
# Path: ay_platform_core/tests/coherence/test_route_catalog.py
# Description: Pins the auth x role x scope test catalog
#              (`tests/e2e/auth_matrix/_catalog.py`) to the live FastAPI
#              routers exposed by every component. Fails the build if a
#              new route is introduced without a corresponding
#              EndpointSpec — the rule from CLAUDE.md §13.
#
# @relation validates:E-100-002
# =============================================================================

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from ay_platform_core.c2_auth.admin_router import router as c2_admin_router
from ay_platform_core.c2_auth.preferences_router import router as c2_preferences_router
from ay_platform_core.c2_auth.projects_router import router as c2_projects_router
from ay_platform_core.c2_auth.router import router as c2_router
from ay_platform_core.c2_auth.ux_router import ux_router as c2_ux_router
from ay_platform_core.c3_conversation.router import router as c3_router
from ay_platform_core.c4_orchestrator.artifacts_router import (
    router as c4_artifacts_router,
)
from ay_platform_core.c4_orchestrator.documents_router import (
    router as c4_documents_router,
)
from ay_platform_core.c4_orchestrator.router import router as c4_router
from ay_platform_core.c4_orchestrator.source_router import (
    router as c4_source_router,
)
from ay_platform_core.c5_requirements.router import router as c5_router
from ay_platform_core.c6_validation.router import router as c6_router
from ay_platform_core.c7_memory.router import router as c7_router
from ay_platform_core.c9_mcp.router import router as c9_router
from tests.e2e.auth_matrix._catalog import ENDPOINTS, Auth, EndpointSpec

pytestmark = pytest.mark.coherence


# Each component's router + the prefix the production app factory mounts
# it under. The catalog stores the full path (prefix + suffix); the
# coherence test reconstructs it from these definitions.
_ROUTERS: list[tuple[str, object, str]] = [
    ("c2_auth", c2_router, "/auth"),
    ("c2_auth", c2_admin_router, "/admin"),
    ("c2_auth", c2_projects_router, "/api/v1/projects"),
    ("c2_auth", c2_preferences_router, "/api/v1/users/me/preferences"),
    ("c2_auth", c2_ux_router, "/ux"),
    ("c3_conversation", c3_router, ""),
    ("c4_orchestrator", c4_router, ""),
    ("c4_orchestrator", c4_artifacts_router, ""),
    ("c4_orchestrator", c4_documents_router, ""),
    ("c4_orchestrator", c4_source_router, ""),
    ("c5_requirements", c5_router, ""),
    ("c6_validation", c6_router, ""),
    ("c7_memory", c7_router, ""),
    ("c9_mcp", c9_router, ""),
]


def _live_routes() -> set[tuple[str, str, str]]:
    """Return the full set of `(component, method, path)` triples
    declared by the platform's component routers."""
    out: set[tuple[str, str, str]] = set()
    for component, router, prefix in _ROUTERS:
        for route in router.routes:  # type: ignore[attr-defined]
            if not isinstance(route, APIRoute):
                continue
            full_path = f"{prefix}{route.path}"
            for method in sorted(route.methods or set()):
                # HEAD is auto-mirrored on GET endpoints (FastAPI default
                # for `api_route`). It carries no separate semantics for
                # the auth matrix; we collapse HEAD into GET for the
                # comparison.
                if method == "HEAD":
                    continue
                # OPTIONS is similarly auto-included by Starlette for
                # CORS; not a business endpoint.
                if method == "OPTIONS":
                    continue
                out.add((component, method, full_path))
    return out


def _catalog_routes() -> set[tuple[str, str, str]]:
    return {(e.component, e.method, e.path) for e in ENDPOINTS}


def test_catalog_matches_live_routes() -> None:
    """Every live route SHALL have an EndpointSpec; every EndpointSpec
    SHALL correspond to a live route. Drift in either direction is a
    bug — see CLAUDE.md §13 ("Auth x role x scope test matrix")."""
    live = _live_routes()
    catalog = _catalog_routes()

    missing_in_catalog = sorted(live - catalog)
    extra_in_catalog = sorted(catalog - live)

    msg_parts: list[str] = []
    if missing_in_catalog:
        msg_parts.append(
            "Routes registered in component routers but ABSENT from "
            "tests/e2e/auth_matrix/_catalog.py — add an EndpointSpec "
            "for each:\n  "
            + "\n  ".join(f"{c} {m} {p}" for c, m, p in missing_in_catalog)
        )
    if extra_in_catalog:
        msg_parts.append(
            "EndpointSpec entries with no matching live route — remove "
            "them or fix the path/method:\n  "
            + "\n  ".join(f"{c} {m} {p}" for c, m, p in extra_in_catalog)
        )
    assert not msg_parts, "\n\n".join(msg_parts)


def test_catalog_entries_have_consistent_role_gates() -> None:
    """A ROLE_GATED endpoint SHALL declare at least one accepted role
    (`accept_roles` or `accept_global_roles`); an AUTHENTICATED or
    OPEN endpoint SHALL declare none. Also, no endpoint SHALL list
    `tenant_manager` in `accept_global_roles` AND in
    `excluded_global_roles` simultaneously (E-100-002 v2 separation
    of duties)."""
    errors: list[str] = []
    for spec in ENDPOINTS:
        accepts = spec.accept_roles + spec.accept_global_roles
        if spec.auth == Auth.ROLE_GATED and not accepts:
            errors.append(
                f"{spec.component} {spec.method} {spec.path}: "
                f"ROLE_GATED endpoint must list at least one accepted role."
            )
        if spec.auth in (Auth.OPEN, Auth.AUTHENTICATED) and accepts:
            errors.append(
                f"{spec.component} {spec.method} {spec.path}: "
                f"{spec.auth} endpoint must NOT list accepted roles "
                f"(use Auth.ROLE_GATED instead)."
            )
        contradiction = set(spec.excluded_global_roles) & set(spec.accept_global_roles)
        if contradiction:
            errors.append(
                f"{spec.component} {spec.method} {spec.path}: "
                f"role(s) appear in BOTH accept_global_roles AND "
                f"excluded_global_roles: {sorted(contradiction)}"
            )

    assert not errors, "\n".join(errors)


def test_each_endpoint_appears_exactly_once() -> None:
    """Sanity: catalog has no duplicate (component, method, path)."""
    seen: dict[tuple[str, str, str], EndpointSpec] = {}
    duplicates: list[tuple[str, str, str]] = []
    for spec in ENDPOINTS:
        key = (spec.component, spec.method, spec.path)
        if key in seen:
            duplicates.append(key)
        seen[key] = spec
    assert not duplicates, f"duplicate EndpointSpec entries: {duplicates}"
