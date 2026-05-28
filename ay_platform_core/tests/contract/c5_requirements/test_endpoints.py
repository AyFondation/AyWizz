# =============================================================================
# File: test_endpoints.py
# Version: 1
# Path: ay_platform_core/tests/contract/c5_requirements/test_endpoints.py
# Description: Contract tests — C5 router exposes every endpoint declared in
#              R-300-024 with the correct HTTP verb, path shape, and
#              response model. Stub endpoints (501) are verified to exist
#              but do not constrain their response models.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from ay_platform_core.c5_requirements.models import (
    DocumentListResponse,
    DocumentPublic,
    EntityListResponse,
    EntityPublic,
)
from ay_platform_core.c5_requirements.router import router


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
class TestEndpointRoster:
    """Every endpoint declared by R-300-024 SHALL be present on the router."""

    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("GET", "/api/v1/projects/{project_id}/requirements/documents"),
        ("GET", "/api/v1/projects/{project_id}/requirements/documents/{slug}"),
        ("POST", "/api/v1/projects/{project_id}/requirements/documents"),
        ("PUT", "/api/v1/projects/{project_id}/requirements/documents/{slug}"),
        ("DELETE", "/api/v1/projects/{project_id}/requirements/documents/{slug}"),
        ("GET", "/api/v1/projects/{project_id}/requirements/entities"),
        ("GET", "/api/v1/projects/{project_id}/requirements/entities/{entity_id}"),
        ("PATCH", "/api/v1/projects/{project_id}/requirements/entities/{entity_id}"),
        ("DELETE", "/api/v1/projects/{project_id}/requirements/entities/{entity_id}"),
        (
            "GET",
            "/api/v1/projects/{project_id}/requirements/entities/{entity_id}/history",
        ),
        (
            "GET",
            "/api/v1/projects/{project_id}/requirements/entities/{entity_id}/versions/{version}",
        ),
        ("GET", "/api/v1/projects/{project_id}/requirements/relations"),
        ("GET", "/api/v1/projects/{project_id}/requirements/tailorings"),
        ("POST", "/api/v1/projects/{project_id}/requirements/import"),
        ("GET", "/api/v1/projects/{project_id}/requirements/export"),
        ("POST", "/api/v1/projects/{project_id}/requirements/reindex"),
        ("GET", "/api/v1/projects/{project_id}/requirements/reindex/{job_id}"),
    ]

    def test_all_endpoints_present(self) -> None:
        routes = _routes()
        for method, path in self.EXPECTED:
            assert path in routes, f"Missing route {path}"
            assert method in routes[path], (
                f"Missing method {method} on {path}; got {routes[path]}"
            )


@pytest.mark.contract
class TestResponseModels:
    def test_list_documents_returns_list_response(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/projects/{project_id}/requirements/documents"
            and "GET" in (r.methods or set())
        )
        assert target.response_model is DocumentListResponse

    def test_get_document_returns_document_public(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/projects/{project_id}/requirements/documents/{slug}"
            and "GET" in (r.methods or set())
        )
        assert target.response_model is DocumentPublic

    def test_list_entities_returns_list_response(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/projects/{project_id}/requirements/entities"
            and "GET" in (r.methods or set())
        )
        assert target.response_model is EntityListResponse

    def test_get_entity_returns_entity_public(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/projects/{project_id}/requirements/entities/{entity_id}"
            and "GET" in (r.methods or set())
        )
        assert target.response_model is EntityPublic

    def test_create_document_is_201(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/projects/{project_id}/requirements/documents"
            and "POST" in (r.methods or set())
        )
        assert target.status_code == 201

    def test_delete_document_is_204(self) -> None:
        target = next(
            r for r in _app().routes
            if isinstance(r, APIRoute)
            and r.path == "/api/v1/projects/{project_id}/requirements/documents/{slug}"
            and "DELETE" in (r.methods or set())
        )
        assert target.status_code == 204
