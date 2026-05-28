# =============================================================================
# File: test_schemas.py
# Version: 1
# Path: ay_platform_core/tests/contract/c7_memory/test_schemas.py
# Description: Contract tests for C7 — registry registration, schema
#              validity, endpoint roster.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from ay_platform_core.c7_memory.models import (
    ChunkPublic,
    RetrievalRequest,
    RetrievalResponse,
    SourcePublic,
)
from ay_platform_core.c7_memory.router import router
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
        for model in (RetrievalRequest, RetrievalResponse, SourcePublic, ChunkPublic):
            assert issubclass(model, BaseModel)


@pytest.mark.contract
class TestContractRegistration:
    EXPECTED: ClassVar[set[str]] = {
        "RetrievalRequest",
        "RetrievalResponse",
        "SourcePublic",
        "ChunkPublic",
    }

    def test_all_expected_contracts_registered(self) -> None:
        names = {c.name for c in find_by_producer("C7_memory")}
        missing = self.EXPECTED - names
        assert not missing, f"Missing C7 contracts: {missing}"

    def test_no_unexpected_contracts(self) -> None:
        names = {c.name for c in find_by_producer("C7_memory")}
        extra = names - self.EXPECTED
        assert not extra, f"Unexpected C7 contracts: {extra}"

    def test_retrieval_consumed_by_downstream_agents(self) -> None:
        for contract in find_by_producer("C7_memory"):
            if contract.name == "RetrievalRequest":
                assert "C4_orchestrator" in contract.consumers
                assert "C3_conversation" in contract.consumers
                return
        pytest.fail("RetrievalRequest not found in registry")

    def test_all_have_consumers(self) -> None:
        for contract in find_by_producer("C7_memory"):
            assert contract.consumers, f"{contract.name} has no declared consumers"


@pytest.mark.contract
class TestEndpointRoster:
    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("POST", "/api/v1/memory/retrieve"),
        ("POST", "/api/v1/memory/projects/{project_id}/sources"),
        ("GET", "/api/v1/memory/projects/{project_id}/sources"),
        ("GET", "/api/v1/memory/projects/{project_id}/sources/{source_id}"),
        ("DELETE", "/api/v1/memory/projects/{project_id}/sources/{source_id}"),
        ("POST", "/api/v1/memory/entities/embed"),
        ("GET", "/api/v1/memory/projects/{project_id}/quota"),
        ("POST", "/api/v1/memory/projects/{project_id}/refresh"),
        ("GET", "/api/v1/memory/refresh/{job_id}"),
        ("GET", "/api/v1/memory/health"),
    ]

    def test_every_endpoint_present(self) -> None:
        routes = _routes()
        for method, path in self.EXPECTED:
            assert path in routes, f"missing {path}"
            assert method in routes[path], (
                f"missing {method} on {path}, got {routes[path]}"
            )

    def test_retrieve_returns_retrieval_response(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/memory/retrieve"
            and "POST" in (r.methods or set())
        )
        assert target.response_model is RetrievalResponse

    def test_delete_source_is_204(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/memory/projects/{project_id}/sources/{source_id}"
            and "DELETE" in (r.methods or set())
        )
        assert target.status_code == 204
