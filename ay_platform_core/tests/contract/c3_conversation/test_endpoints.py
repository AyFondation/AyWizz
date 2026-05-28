# =============================================================================
# File: test_endpoints.py
# Version: 1
# Path: ay_platform_core/tests/contract/c3_conversation/test_endpoints.py
# Description: Contract tests — C3 router exposes the declared endpoints with
#              correct HTTP methods, prefixes, and response models.
# =============================================================================

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from ay_platform_core.c3_conversation.models import ConversationListResponse
from ay_platform_core.c3_conversation.router import router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _routes() -> dict[str, set[str]]:
    """Return {path: {methods}} for all APIRoutes — accumulates multiple routes per path."""
    result: dict[str, set[str]] = {}
    for route in _app().routes:
        if isinstance(route, APIRoute):
            result.setdefault(route.path, set()).update(
                m.upper() for m in (route.methods or set())
            )
    return result


@pytest.mark.contract
class TestEndpointRoster:
    EXPECTED: ClassVar[list[tuple[str, str]]] = [
        ("GET", "/api/v1/conversations"),
        ("POST", "/api/v1/conversations"),
        ("GET", "/api/v1/conversations/{conversation_id}"),
        ("PATCH", "/api/v1/conversations/{conversation_id}"),
        ("DELETE", "/api/v1/conversations/{conversation_id}"),
        ("GET", "/api/v1/conversations/{conversation_id}/messages"),
        ("POST", "/api/v1/conversations/{conversation_id}/messages"),
        ("GET", "/api/v1/conversations/{conversation_id}/events"),
    ]

    def test_all_expected_endpoints_present(self) -> None:
        routes = _routes()
        for method, path in self.EXPECTED:
            assert path in routes, f"Path '{path}' not found in router"
            assert method in routes[path], (
                f"Method {method} not declared on {path} — got {routes[path]}"
            )

    def test_no_untyped_dict_response_models(self) -> None:
        for route in _app().routes:
            if not isinstance(route, APIRoute):
                continue
            rm = route.response_model
            if rm is None:
                continue  # 204 / StreamingResponse — OK
            assert rm is not dict, f"{route.path}: response_model is raw dict"

    def test_list_endpoint_returns_list_response(self) -> None:
        list_route = next(
            (
                r for r in _app().routes
                if isinstance(r, APIRoute)
                and r.path == "/api/v1/conversations"
                and "GET" in (r.methods or set())
            ),
            None,
        )
        assert list_route is not None, "GET /api/v1/conversations not found"
        assert list_route.response_model is ConversationListResponse

    def test_create_endpoint_is_201(self) -> None:
        routes = {r.path: r for r in _app().routes if isinstance(r, APIRoute)}
        matching = [
            r for r in routes.values()
            if r.path == "/api/v1/conversations" and "POST" in (r.methods or set())
        ]
        assert matching, "POST /api/v1/conversations not found"
        assert matching[0].status_code == 201

    def test_delete_endpoint_is_204(self) -> None:
        for route in _app().routes:
            if (
                isinstance(route, APIRoute)
                and route.path.endswith("/{conversation_id}")
                and "DELETE" in (route.methods or set())
            ):
                assert route.status_code == 204
                return
        pytest.fail("DELETE /{conversation_id} not found")
