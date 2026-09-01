# =============================================================================
# File: test_route_declarations.py
# Version: 4
# Path: ay_platform_core/tests/contract/c1_gateway/test_route_declarations.py
# Description: Contract tests — Traefik router and middleware wiring.
#              Verifies that every required route is declared, that
#              rate-limiting is applied exclusively to auth login/token,
#              and that forward-auth protects all /api/* and /uploads routes.
#              v2: `TestUiGovernancePages` — every `/admin/*` UI page prefix
#              routes to the UI without forward-auth and beats the c2 /admin
#              catch-all (regression guard for the embedding pages 401).
# @relation R-100-039 R-100-042 A-C1-1
# =============================================================================

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, cast

import pytest
import yaml

INFRA_ROOT = Path(__file__).parent.parent.parent.parent.parent / "infra" / "c1_gateway"
DYNAMIC_DIR = INFRA_ROOT / "dynamic"


def _routers() -> dict[str, Any]:
    raw = (DYNAMIC_DIR / "routers.yml").read_text(encoding="utf-8")
    cfg = cast(dict[str, Any], yaml.safe_load(raw))
    return cast(dict[str, Any], cfg["http"]["routers"])


@pytest.mark.contract
class TestRouteCoverage:
    """Every component exposed by C1 has a declared router."""

    EXPECTED_SERVICES: ClassVar[set[str]] = {"c2", "c3", "c4", "c5", "c6", "c12"}

    def test_all_services_have_at_least_one_router(self) -> None:
        routers = _routers()
        routed_services = {r["service"] for r in routers.values()}
        missing = self.EXPECTED_SERVICES - routed_services
        assert not missing, f"No router found for services: {missing}"

    def test_auth_prefix_covered(self) -> None:
        routers = _routers()
        auth_routers = [r for r in routers.values() if r["service"] == "c2"]
        rules = [r["rule"] for r in auth_routers]
        assert any("/auth" in rule for rule in rules), (
            "No router covering /auth prefix for C2"
        )

    def test_conversations_prefix_covered(self) -> None:
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c3"]
        assert any("/api/v1/conversations" in rule for rule in rules)

    def test_orchestrator_prefix_covered(self) -> None:
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c4"]
        assert any("/api/v1/orchestrator" in rule for rule in rules)

    def test_requirements_prefix_covered(self) -> None:
        # C5 routes live under /api/v1/projects/<pid>/requirements/* — the
        # Traefik rule therefore targets the /api/v1/projects prefix.
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c5"]
        assert any("/api/v1/projects" in rule for rule in rules)

    def test_validation_prefix_covered(self) -> None:
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c6"]
        assert any("/api/v1/validation" in rule for rule in rules)

    def test_memory_prefix_covered(self) -> None:
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c7"]
        assert any("/api/v1/memory" in rule for rule in rules)

    def test_mcp_prefix_covered(self) -> None:
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c9"]
        assert any("/api/v1/mcp" in rule for rule in rules)

    def test_uploads_prefix_covered(self) -> None:
        routers = _routers()
        rules = [r["rule"] for r in routers.values() if r["service"] == "c12"]
        assert any("/uploads" in rule for rule in rules)


@pytest.mark.contract
class TestRateLimitWiring:
    """R-100-039: rate-limit-auth applied to /auth/login and /auth/token only."""

    def test_login_router_has_rate_limit(self) -> None:
        routers = _routers()
        login_routers = [
            r for r in routers.values()
            if "auth/login" in r["rule"] or "auth/token" in r["rule"]
        ]
        assert login_routers, "No router found for /auth/login or /auth/token"
        for r in login_routers:
            mw = r.get("middlewares", [])
            assert "rate-limit-auth" in mw, (
                f"rate-limit-auth missing from router: {r['rule']}"
            )

    def test_non_auth_routers_do_not_have_rate_limit(self) -> None:
        routers = _routers()
        for name, r in routers.items():
            if r["service"] in ("c3", "c4", "c5", "c6", "c12"):
                mw = r.get("middlewares", [])
                assert "rate-limit-auth" not in mw, (
                    f"rate-limit-auth incorrectly applied to {name} (service={r['service']})"
                )


@pytest.mark.contract
class TestForwardAuthWiring:
    """forward-auth-c2 must protect all non-auth routes."""

    PROTECTED_SERVICES: ClassVar[set[str]] = {"c3", "c4", "c5", "c6", "c12"}

    def test_protected_routes_have_forward_auth(self) -> None:
        routers = _routers()
        for name, r in routers.items():
            if r["service"] in self.PROTECTED_SERVICES:
                mw = r.get("middlewares", [])
                assert "forward-auth-c2" in mw, (
                    f"forward-auth-c2 missing from router '{name}' (service={r['service']})"
                )

    def test_auth_routes_do_not_have_forward_auth(self) -> None:
        """Routes targeting `/auth/*` SHALL NOT carry the forward-auth
        middleware (would loop : forward-auth itself calls /auth/verify).

        Other C2 routes (`/admin/*`, `/api/v1/projects/*`, `/ux/*`)
        target the c2 service too but live on distinct paths — they
        legitimately consume the forward-auth headers, so they MUST
        have the middleware. The assertion narrows to /auth/ routes
        only.
        """
        routers = _routers()
        for name, r in routers.items():
            if r["service"] != "c2":
                continue
            rule = r.get("rule", "")
            if "/auth" not in rule:
                continue
            mw = r.get("middlewares", [])
            assert "forward-auth-c2" not in mw, (
                f"forward-auth-c2 must not be applied to C2 /auth router '{name}' "
                "— this would create an auth loop"
            )


@pytest.mark.contract
class TestPriorityOrdering:
    """More specific auth routers must have higher priority than the catch-all."""

    def test_login_token_priority_higher_than_auth_catch_all(self) -> None:
        routers = _routers()
        specific = [
            r for r in routers.values()
            if ("auth/login" in r["rule"] or "auth/token" in r["rule"])
            and r["service"] == "c2"
        ]
        catch_all = [
            r for r in routers.values()
            if "PathPrefix" in r["rule"] and "/auth" in r["rule"] and r["service"] == "c2"
            and "auth/login" not in r["rule"] and "auth/token" not in r["rule"]
        ]
        if specific and catch_all:
            for s in specific:
                for c in catch_all:
                    assert s.get("priority", 0) > c.get("priority", 0), (
                        "Login/token routers must have higher priority than the /auth catch-all"
                    )


@pytest.mark.contract
class TestUiGovernancePages:
    """Next.js governance PAGE paths under `/admin/*` collide with the C2
    `/admin` API prefix. Each MUST be routed to the UI service, WITHOUT
    forward-auth (browser page navigation carries no forward-auth headers),
    and at a HIGHER priority than the c2 `/admin` catch-all — otherwise the
    page nav falls through to c2 [forward-auth] and returns
    `{"detail":"Not authenticated"}`. Regression guard for the embedding
    registry pages, which had been forgotten when `/admin/llm` was added.
    """

    # Every UI admin governance page family. Add a row when a new
    # `/admin/<x>-*` page section lands.
    UI_PAGE_PREFIXES: ClassVar[tuple[str, ...]] = ("/admin/llm", "/admin/embedding")

    def _c2_admin_catch_all_priority(self) -> int:
        for r in _routers().values():
            if r["service"] == "c2" and r.get("rule") == "PathPrefix(`/admin`)":
                return int(r.get("priority", 0))
        return 0

    @pytest.mark.parametrize("prefix", UI_PAGE_PREFIXES)
    def test_ui_admin_page_reaches_ui_without_forward_auth(self, prefix: str) -> None:
        routers = _routers()
        match = [
            (name, r) for name, r in routers.items()
            if r.get("rule") == f"PathPrefix(`{prefix}`)"
        ]
        assert match, (
            f"No router declares the UI governance page prefix {prefix!r} — "
            "page nav will fall through to /admin→c2 and 401 'Not authenticated'"
        )
        catch_all_prio = self._c2_admin_catch_all_priority()
        for name, r in match:
            assert r["service"] == "ui", (
                f"router '{name}' for {prefix!r} must target the UI, not {r['service']!r}"
            )
            assert "forward-auth-c2" not in r.get("middlewares", []), (
                f"router '{name}' for {prefix!r} must NOT carry forward-auth "
                "(browser page nav has no forward-auth headers → would 401)"
            )
            assert r.get("priority", 0) > catch_all_prio, (
                f"router '{name}' ({prefix!r}, priority {r.get('priority', 0)}) must "
                f"beat the c2 /admin catch-all (priority {catch_all_prio})"
            )


@pytest.mark.contract
class TestBackupRoute:
    """The C16 backups subpath `/api/v1/projects/{pid}/backups` MUST route to
    c16-backup WITH forward-auth and at a higher priority than the generic
    `/api/v1/projects` → c2 (priority 50), else it falls through to c2 → 404
    (the storage-snapshot class of bug)."""

    def test_backups_routes_to_c16_with_forward_auth(self) -> None:
        routers = _routers()
        match = [
            r for r in routers.values()
            if "backups" in r.get("rule", "") and "projects" in r.get("rule", "")
        ]
        assert match, "no router declares the /projects/{pid}/backups subpath"
        for r in match:
            assert r["service"] == "c16-backup", (
                f"backups route must target c16-backup, not {r['service']!r}"
            )
            assert "forward-auth-c2" in r.get("middlewares", [])
            assert r.get("priority", 0) > 50  # beats the generic c2-projects


@pytest.mark.contract
class TestC8AdminApiRoutes:
    """Every c8-admin governance API prefix under `/admin/v1/*` MUST be routed
    to the c8-admin service WITH forward-auth and at a priority that beats the
    c2 `/admin` catch-all — otherwise the API call falls through to c2 and 404s.
    Regression guard for the storage-snapshot 404 (and the earlier missing
    embedding/quota API routes): adding a new c8-admin `/admin/v1/<x>` surface
    requires declaring its gateway route here too.
    """

    # Every c8-admin API prefix. Add a row when a new `/admin/v1/<x>` c8-admin
    # surface lands (llm, quota, storage, …).
    C8_ADMIN_API_PREFIXES: ClassVar[tuple[str, ...]] = (
        "/admin/v1/llm",
        "/admin/v1/quota",
        "/admin/v1/storage",
    )

    def _c2_admin_catch_all_priority(self) -> int:
        for r in _routers().values():
            if r["service"] == "c2" and r.get("rule") == "PathPrefix(`/admin`)":
                return int(r.get("priority", 0))
        return 0

    @pytest.mark.parametrize("prefix", C8_ADMIN_API_PREFIXES)
    def test_c8_admin_api_prefix_routed_with_forward_auth(self, prefix: str) -> None:
        routers = _routers()
        match = [
            (name, r) for name, r in routers.items()
            if r.get("rule") == f"PathPrefix(`{prefix}`)"
        ]
        assert match, (
            f"No router declares the c8-admin API prefix {prefix!r} — the call "
            "falls through to /admin→c2 and 404s"
        )
        catch_all_prio = self._c2_admin_catch_all_priority()
        for name, r in match:
            assert r["service"] == "c8-admin", (
                f"router '{name}' for {prefix!r} must target c8-admin, not "
                f"{r['service']!r}"
            )
            assert "forward-auth-c2" in r.get("middlewares", []), (
                f"router '{name}' for {prefix!r} must carry forward-auth"
            )
            assert r.get("priority", 0) > catch_all_prio, (
                f"router '{name}' ({prefix!r}, priority {r.get('priority', 0)}) must "
                f"beat the c2 /admin catch-all (priority {catch_all_prio})"
            )
