# =============================================================================
# File: test_factories.py
# Version: 1
# Path: ay_platform_core/tests/unit/_app_factories/test_factories.py
# Description: Smoke tests for the per-component `main.py` app factories.
#              These tests do NOT connect to any backing service — they
#              instantiate the FastAPI app with no lifespan and assert the
#              router is mounted. Lifespan runs only when the app is served,
#              so constructing the app is cheap and network-free.
#              The integration/system tiers cover the running-container path.
# =============================================================================

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from ay_platform_core._mock_llm.main import create_app as make_mock_llm
from ay_platform_core.c2_auth.main import create_app as make_c2
from ay_platform_core.c3_conversation.main import create_app as make_c3
from ay_platform_core.c4_orchestrator.main import create_app as make_c4
from ay_platform_core.c5_requirements.main import create_app as make_c5
from ay_platform_core.c6_validation.main import create_app as make_c6
from ay_platform_core.c7_memory.main import create_app as make_c7
from ay_platform_core.c9_mcp.main import create_app as make_c9


def _routes(app: FastAPI) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


@pytest.mark.unit
class TestAppFactories:
    def test_c2_factory_mounts_auth_routes(self) -> None:
        app = make_c2()
        paths = _routes(app)
        assert any("/auth" in p for p in paths)
        assert "/health" in paths

    def test_c3_factory_mounts_conversation_routes(self) -> None:
        app = make_c3()
        paths = _routes(app)
        assert any("/api/v1/conversations" in p for p in paths)
        assert "/health" in paths

    def test_c4_factory_mounts_orchestrator_routes(self) -> None:
        app = make_c4()
        paths = _routes(app)
        assert any("/api/v1/orchestrator" in p for p in paths)
        assert "/health" in paths

    def test_c5_factory_mounts_requirements_routes(self) -> None:
        app = make_c5()
        paths = _routes(app)
        assert any("/api/v1/projects" in p for p in paths)
        assert "/health" in paths

    def test_c6_factory_mounts_validation_routes(self) -> None:
        app = make_c6()
        paths = _routes(app)
        assert any("/api/v1/validation" in p for p in paths)
        assert "/health" in paths

    def test_c7_factory_mounts_memory_routes(self) -> None:
        app = make_c7()
        paths = _routes(app)
        assert any("/api/v1/memory" in p for p in paths)
        assert "/health" in paths

    def test_c9_factory_mounts_mcp_routes(self) -> None:
        app = make_c9()
        paths = _routes(app)
        assert any("/api/v1/mcp" in p for p in paths)
        assert "/health" in paths

    def test_mock_llm_factory_mounts_expected_routes(self) -> None:
        app = make_mock_llm()
        paths = _routes(app)
        assert "/v1/chat/completions" in paths
        assert "/admin/enqueue" in paths
        assert "/admin/reset" in paths
        assert "/admin/calls" in paths
        assert "/health" in paths
