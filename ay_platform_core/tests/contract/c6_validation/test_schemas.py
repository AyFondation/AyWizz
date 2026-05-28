# =============================================================================
# File: test_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c6_validation/test_schemas.py
# Description: Contract tests for C6 — registry registration, endpoint roster,
#              Pydantic schema validity of public models.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from ay_platform_core.c6_validation.models import (
    Finding,
    FindingPage,
    PluginDescriptor,
    RunTriggerRequest,
    RunTriggerResponse,
    ValidationRun,
)
from ay_platform_core.c6_validation.plugin.registry import get_registry
from ay_platform_core.c6_validation.router import router
from tests.fixtures.contract_registry import find_by_producer


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _routes() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for route in _app().routes:
        if isinstance(route, APIRoute):
            result.setdefault(route.path, set()).update(
                m.upper() for m in (route.methods or set())
            )
    return result


@pytest.mark.contract
class TestPublicSchemas:
    def test_all_are_pydantic(self) -> None:
        for model in (
            Finding,
            PluginDescriptor,
            RunTriggerRequest,
            RunTriggerResponse,
            ValidationRun,
        ):
            assert issubclass(model, BaseModel)


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {
        "RunTriggerRequest",
        "RunTriggerResponse",
        "ValidationRun",
        "Finding",
        "PluginDescriptor",
    }

    def test_all_expected_contracts_registered(self) -> None:
        names = {c.name for c in find_by_producer("C6_validation")}
        missing = self.EXPECTED - names
        assert not missing, f"Missing C6 contracts: {missing}"

    def test_no_unexpected_contracts(self) -> None:
        names = {c.name for c in find_by_producer("C6_validation")}
        extra = names - self.EXPECTED
        assert not extra, f"Unexpected C6 contracts: {extra}"

    def test_runs_are_consumable_by_downstream(self) -> None:
        for contract in find_by_producer("C6_validation"):
            if contract.name == "ValidationRun":
                assert "C4_orchestrator" in contract.consumers
                assert "C9_mcp" in contract.consumers
                return
        pytest.fail("ValidationRun not registered")

    def test_all_have_consumers(self) -> None:
        for contract in find_by_producer("C6_validation"):
            assert contract.consumers, f"{contract.name} has no declared consumers"


@pytest.mark.contract
class TestEndpointRoster:
    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("GET", "/api/v1/validation/plugins"),
        ("GET", "/api/v1/validation/domains"),
        ("POST", "/api/v1/validation/runs"),
        ("GET", "/api/v1/validation/runs/{run_id}"),
        ("GET", "/api/v1/validation/runs/{run_id}/findings"),
        ("GET", "/api/v1/validation/findings/{finding_id}"),
        ("GET", "/api/v1/validation/health"),
    ]

    def test_every_endpoint_present(self) -> None:
        routes = _routes()
        for method, path in self.EXPECTED:
            assert path in routes, f"missing {path}"
            assert method in routes[path], (
                f"missing {method} on {path}, got {routes[path]}"
            )

    def test_trigger_is_202(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/validation/runs"
            and "POST" in (r.methods or set())
        )
        assert target.status_code == 202

    def test_findings_returns_page(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/validation/runs/{run_id}/findings"
        )
        assert target.response_model is FindingPage


@pytest.mark.contract
class TestBuiltinPluginRegistered:
    def test_code_plugin_is_registered(self) -> None:
        domains = get_registry().domains()
        assert "code" in domains

    def test_code_plugin_declares_nine_checks(self) -> None:
        plugins = get_registry().plugins_for_domain("code")
        assert len(plugins) == 1
        assert len(plugins[0].describe().checks) == 9
