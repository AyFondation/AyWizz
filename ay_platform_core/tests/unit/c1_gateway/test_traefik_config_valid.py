# =============================================================================
# File: test_traefik_config_valid.py
# Version: 1
# Path: ay_platform_core/tests/unit/c1_gateway/test_traefik_config_valid.py
# Description: Unit tests — Traefik YAML config structural validity.
#              Parses all config files and asserts required keys, types,
#              and values are present without spinning up any container.
# @relation R-100-039 R-100-042 R-100-100
# =============================================================================

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

INFRA_ROOT = Path(__file__).parent.parent.parent.parent.parent / "infra" / "c1_gateway"
STATIC_CONFIG = INFRA_ROOT / "traefik.yml"
DYNAMIC_DIR = INFRA_ROOT / "dynamic"


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.mark.unit
class TestStaticConfig:
    def test_file_exists(self) -> None:
        assert STATIC_CONFIG.exists(), f"traefik.yml not found at {STATIC_CONFIG}"

    def test_entrypoints_defined(self) -> None:
        cfg = _load(STATIC_CONFIG)
        eps = cfg.get("entryPoints", {})
        assert "web" in eps, "entryPoint 'web' missing"
        assert "traefik" in eps, "entryPoint 'traefik' (dashboard) missing"

    def test_web_entrypoint_port(self) -> None:
        cfg = _load(STATIC_CONFIG)
        address = cfg["entryPoints"]["web"]["address"]
        assert ":80" in address, f"web entryPoint should bind to port 80, got: {address}"

    def test_file_provider_configured(self) -> None:
        cfg = _load(STATIC_CONFIG)
        providers = cfg.get("providers", {})
        assert "file" in providers, "File provider missing from providers"
        file_provider = providers["file"]
        assert "directory" in file_provider, "File provider must specify 'directory'"
        assert file_provider.get("watch") is True, "File provider should have watch: true"

    def test_api_dashboard_enabled(self) -> None:
        cfg = _load(STATIC_CONFIG)
        api = cfg.get("api", {})
        assert api.get("dashboard") is True, "Dashboard should be enabled for local dev"


@pytest.mark.unit
class TestMiddlewaresConfig:
    def test_file_exists(self) -> None:
        assert (DYNAMIC_DIR / "middlewares.yml").exists()

    def test_rate_limit_auth_defined(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        middlewares = cfg["http"]["middlewares"]
        assert "rate-limit-auth" in middlewares, "rate-limit-auth middleware missing"

    def test_rate_limit_auth_values(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        rl = cfg["http"]["middlewares"]["rate-limit-auth"]["rateLimit"]
        assert rl.get("average") == 10, "rate-limit average must be 10 req/min (R-100-039)"
        assert rl.get("period") == "1m", "rate-limit period must be 1m (R-100-039)"
        assert "burst" in rl, "rate-limit burst must be configured"

    def test_forward_auth_c2_defined(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        middlewares = cfg["http"]["middlewares"]
        assert "forward-auth-c2" in middlewares, "forward-auth-c2 middleware missing"

    def test_forward_auth_points_to_verify(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        fa = cfg["http"]["middlewares"]["forward-auth-c2"]["forwardAuth"]
        assert "/auth/verify" in fa["address"], (
            "forward-auth must point to C2 /auth/verify"
        )

    def test_forward_auth_propagates_user_headers(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        fa = cfg["http"]["middlewares"]["forward-auth-c2"]["forwardAuth"]
        propagated = fa.get("authResponseHeaders", [])
        assert "X-User-Id" in propagated, "X-User-Id must be propagated from C2 verify"
        assert "X-User-Roles" in propagated, "X-User-Roles must be propagated from C2 verify"

    def test_secure_headers_defined(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        middlewares = cfg["http"]["middlewares"]
        assert "secure-headers" in middlewares, "secure-headers middleware missing"

    def test_secure_headers_frame_deny(self) -> None:
        cfg = _load(DYNAMIC_DIR / "middlewares.yml")
        headers = cfg["http"]["middlewares"]["secure-headers"]["headers"]
        assert headers.get("frameDeny") is True, "X-Frame-Options: DENY must be set"


@pytest.mark.unit
class TestServicesConfig:
    def test_file_exists(self) -> None:
        assert (DYNAMIC_DIR / "services.yml").exists()

    def test_all_components_defined(self) -> None:
        cfg = _load(DYNAMIC_DIR / "services.yml")
        services = cfg["http"]["services"]
        for component in ("c2", "c3", "c4", "c5", "c6", "c12"):
            assert component in services, f"Service '{component}' missing from services.yml"

    def test_services_have_load_balancer(self) -> None:
        cfg = _load(DYNAMIC_DIR / "services.yml")
        for name, svc in cfg["http"]["services"].items():
            assert "loadBalancer" in svc, f"Service '{name}' missing loadBalancer"
            servers = svc["loadBalancer"].get("servers", [])
            assert servers, f"Service '{name}' has no backend servers"
            assert "url" in servers[0], f"Service '{name}' server missing 'url'"


@pytest.mark.unit
class TestRoutersConfig:
    def test_file_exists(self) -> None:
        assert (DYNAMIC_DIR / "routers.yml").exists()

    def test_all_router_files_are_valid_yaml(self) -> None:
        for fname in ("routers.yml", "middlewares.yml", "services.yml"):
            path = DYNAMIC_DIR / fname
            try:
                _load(path)
            except yaml.YAMLError as exc:
                pytest.fail(f"{fname} is not valid YAML: {exc}")
