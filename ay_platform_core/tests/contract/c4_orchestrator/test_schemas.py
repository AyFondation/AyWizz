# =============================================================================
# File: test_schemas.py
# Version: 2
# Path: ay_platform_core/tests/contract/c4_orchestrator/test_schemas.py
# Description: Contract tests — C4 public schemas + registry + endpoint roster.
#              v2 aligns EXPECTED with the Tranche-B / sub-agent contracts
#              registered in contract_registry.py (trace/steer, source-files,
#              sub-agent bundle).
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from ay_platform_core.c4_orchestrator.models import (
    AgentCompletion,
    DomainDescriptor,
    RunPublic,
)
from ay_platform_core.c4_orchestrator.router import router
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
    def test_run_public_is_pydantic(self) -> None:
        assert issubclass(RunPublic, BaseModel)

    def test_agent_completion_is_pydantic(self) -> None:
        assert issubclass(AgentCompletion, BaseModel)

    def test_domain_descriptor_is_pydantic(self) -> None:
        assert issubclass(DomainDescriptor, BaseModel)


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {
        "RunPublic",
        "AgentCompletion",
        "DomainDescriptor",
        # Project artifacts surface — R-200-131..133
        "ArtifactRunPublic",
        "ArtifactRunList",
        "ArtifactNode",
        "ArtifactTree",
        # Project versioning proxy — R-200-147
        "ArtifactCommit",
        "ArtifactCommitList",
        # Chat-direct DocGen document API — D-015 / R-200-153..156
        "DocumentWrite",
        "DocumentUpdate",
        "DocumentRef",
        "DocumentList",
        # Run trace + steer surface (Tranche B) — E-200-006/007, R-200-203
        "TraceEvent",
        "RunSteer",
        # Source-files surface (Tranche B) — R-200-170..174
        "SourceTreeNode",
        "SourceFileMeta",
        # Sub-agent context bundle contracts — R-200-033
        "TaskEnvelope",
        "SubAgentRunReport",
    }

    def test_all_expected_contracts_registered(self) -> None:
        names = {c.name for c in find_by_producer("C4_orchestrator")}
        assert not (self.EXPECTED - names), f"missing: {self.EXPECTED - names}"

    def test_no_unexpected_contracts(self) -> None:
        names = {c.name for c in find_by_producer("C4_orchestrator")}
        assert not (names - self.EXPECTED), f"extra: {names - self.EXPECTED}"

    def test_run_public_consumed_by_conversation_and_gateway(self) -> None:
        for contract in find_by_producer("C4_orchestrator"):
            if contract.name == "RunPublic":
                assert "C3_conversation" in contract.consumers
                assert "C1_gateway" in contract.consumers
                return
        pytest.fail("RunPublic contract missing from registry")


@pytest.mark.contract
class TestEndpointRoster:
    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("POST", "/api/v1/orchestrator/runs"),
        ("GET", "/api/v1/orchestrator/runs/{run_id}"),
        ("POST", "/api/v1/orchestrator/runs/{run_id}/feedback"),
        ("POST", "/api/v1/orchestrator/runs/{run_id}/resume"),
    ]

    def test_every_endpoint_present(self) -> None:
        routes = _routes()
        for method, path in self.EXPECTED:
            assert path in routes, f"missing path {path}"
            assert method in routes[path], (
                f"missing method {method} on {path}; got {routes[path]}"
            )

    def test_create_run_returns_run_public(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/orchestrator/runs"
            and "POST" in (r.methods or set())
        )
        assert target.response_model is RunPublic
        assert target.status_code == 201
