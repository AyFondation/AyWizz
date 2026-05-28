# =============================================================================
# File: test_client_headers.py
# Version: 1
# Path: ay_platform_core/tests/contract/c8_llm/test_client_headers.py
# Description: Contract tests for LLMGatewayClient — the mandatory headers
#              from R-800-013 are always set, optional headers are only
#              emitted when the caller provides them, and invalid values
#              are rejected before any network I/O.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.config import ClientSettings


def _client(**kwargs: object) -> LLMGatewayClient:
    return LLMGatewayClient(
        ClientSettings(gateway_url="http://c8:8000/v1"),
        bearer_token="test-token",
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.contract
class TestHeaderAssembly:
    def test_mandatory_headers_always_present(self) -> None:
        headers = _client()._headers(
            agent_name="planner",
            session_id="s-1",
            tenant_id=None,
            project_id=None,
            phase=None,
            sub_agent_id=None,
            cache_hint=None,
            bearer_token=None,
        )
        assert headers["X-Agent-Name"] == "planner"
        assert headers["X-Session-Id"] == "s-1"
        assert headers["Authorization"] == "Bearer test-token"

    def test_missing_agent_name_raises(self) -> None:
        with pytest.raises(ValueError, match="X-Agent-Name"):
            _client()._headers(
                agent_name="",
                session_id="s-1",
                tenant_id=None,
                project_id=None,
                phase=None,
                sub_agent_id=None,
                cache_hint=None,
                bearer_token=None,
            )

    def test_missing_session_id_raises(self) -> None:
        with pytest.raises(ValueError, match="X-Session-Id"):
            _client()._headers(
                agent_name="planner",
                session_id="",
                tenant_id=None,
                project_id=None,
                phase=None,
                sub_agent_id=None,
                cache_hint=None,
                bearer_token=None,
            )

    def test_missing_bearer_rejected(self) -> None:
        client = LLMGatewayClient(
            ClientSettings(gateway_url="http://c8:8000/v1"),
            bearer_token=None,
        )
        with pytest.raises(ValueError, match="bearer token"):
            client._headers(
                agent_name="planner",
                session_id="s-1",
                tenant_id=None,
                project_id=None,
                phase=None,
                sub_agent_id=None,
                cache_hint=None,
                bearer_token=None,
            )

    def test_optional_headers_omitted_when_none(self) -> None:
        headers = _client()._headers(
            agent_name="planner",
            session_id="s-1",
            tenant_id=None,
            project_id=None,
            phase=None,
            sub_agent_id=None,
            cache_hint=None,
            bearer_token=None,
        )
        assert "X-Tenant-Id" not in headers
        assert "X-Phase" not in headers
        assert "X-Cache-Hint" not in headers

    def test_cache_hint_validated(self) -> None:
        with pytest.raises(ValueError, match="X-Cache-Hint"):
            _client()._headers(
                agent_name="planner",
                session_id="s-1",
                tenant_id=None,
                project_id=None,
                phase=None,
                sub_agent_id=None,
                cache_hint="INVALID",
                bearer_token=None,
            )

    def test_all_optional_headers_propagate(self) -> None:
        headers = _client()._headers(
            agent_name="planner",
            session_id="s-1",
            tenant_id="t-1",
            project_id="p-1",
            phase="plan",
            sub_agent_id="sub-1",
            cache_hint="static",
            bearer_token="override",
        )
        assert headers["X-Tenant-Id"] == "t-1"
        assert headers["X-Project-Id"] == "p-1"
        assert headers["X-Phase"] == "plan"
        assert headers["X-Sub-Agent-Id"] == "sub-1"
        assert headers["X-Cache-Hint"] == "static"
        # per-call bearer overrides the default
        assert headers["Authorization"] == "Bearer override"
