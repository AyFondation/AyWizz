# =============================================================================
# File: test_agent_route_resolver.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_agent_route_resolver.py
# Description: Unit tests for the client-side per-agent route resolver
#              introduced in `LLMGatewayClient` v3 (R-800-030 v1 note).
#              Cover :
#                - precedence : explicit payload.model > agent_routes >
#                  settings.default_model > leave-unset ;
#                - `_load_agent_routes` priority : inline JSON wins over
#                  YAML, malformed inputs degrade to empty dict ;
#                - constructor override accepted.
#              No I/O against the proxy — the resolver is a pure
#              transform on the request payload.
#
# @relation validates:R-800-030
# =============================================================================

from __future__ import annotations

import json
from pathlib import Path

from ay_platform_core.c8_llm.client import LLMGatewayClient, _load_agent_routes
from ay_platform_core.c8_llm.config import ClientSettings
from ay_platform_core.c8_llm.models import ChatCompletionRequest, ChatMessage, ChatRole


def _settings(**overrides: object) -> ClientSettings:
    base: dict[str, object] = {
        "gateway_url": "http://test/v1",
        "default_model": "",
    }
    base.update(overrides)
    return ClientSettings(**base)  # type: ignore[arg-type]


def _payload(model: str | None = None) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hi")],
        model=model,
    )


class TestResolvePrecedence:
    def test_explicit_model_wins(self) -> None:
        client = LLMGatewayClient(
            _settings(default_model="fallback-1"),
            agent_routes={"c3-rag": "from-agent-routes"},
        )
        out = client._resolve_model(_payload(model="explicit-x"), "c3-rag")
        assert out.model == "explicit-x"

    def test_agent_route_used_when_no_explicit_model(self) -> None:
        client = LLMGatewayClient(
            _settings(default_model="fallback-1"),
            agent_routes={"c3-rag": "from-agent-routes"},
        )
        out = client._resolve_model(_payload(), "c3-rag")
        assert out.model == "from-agent-routes"

    def test_default_model_used_when_agent_route_missing(self) -> None:
        client = LLMGatewayClient(
            _settings(default_model="fallback-1"),
            agent_routes={"c3-rag": "from-agent-routes"},
        )
        out = client._resolve_model(_payload(), "c4-architect")  # no route
        assert out.model == "fallback-1"

    def test_unset_when_no_route_and_no_default(self) -> None:
        client = LLMGatewayClient(_settings(default_model=""), agent_routes={})
        out = client._resolve_model(_payload(), "any-agent")
        assert out.model is None

    def test_payload_not_mutated(self) -> None:
        client = LLMGatewayClient(
            _settings(default_model="fallback-1"),
            agent_routes={"c3-rag": "from-agent-routes"},
        )
        p = _payload()
        out = client._resolve_model(p, "c3-rag")
        assert p.model is None  # original untouched
        assert out is not p


class TestLoadAgentRoutes:
    def test_empty_when_no_source_configured(self) -> None:
        assert _load_agent_routes(_settings()) == {}

    def test_inline_json_wins_over_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "lite.yaml"
        yaml_path.write_text("agent_routes:\n  c3-rag: yaml-model\n")
        inline = json.dumps({"c3-rag": "inline-model"})
        routes = _load_agent_routes(
            _settings(
                agent_routes_yaml_path=str(yaml_path),
                agent_routes_inline=inline,
            ),
        )
        assert routes == {"c3-rag": "inline-model"}

    def test_yaml_loads_agent_routes_section(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "lite.yaml"
        yaml_path.write_text(
            "model_list:\n"
            "  - model_name: claude-haiku-fast\n"
            "    litellm_params:\n"
            "      model: anthropic/claude-haiku-4-5\n"
            "    model_info:\n"
            "      display_name: Haiku\n"
            "      features: [chat_completion]\n"
            "      context_window: 200000\n"
            "      cost_per_million_input: 0.8\n"
            "      cost_per_million_output: 4.0\n"
            "agent_routes:\n"
            "  c3-docgen: claude-haiku-fast\n"
            "  c3-rag: claude-haiku-fast\n",
        )
        routes = _load_agent_routes(
            _settings(agent_routes_yaml_path=str(yaml_path)),
        )
        assert routes == {
            "c3-docgen": "claude-haiku-fast",
            "c3-rag": "claude-haiku-fast",
        }

    def test_inline_malformed_json_degrades_to_empty(self) -> None:
        routes = _load_agent_routes(
            _settings(agent_routes_inline="not json {{{"),
        )
        assert routes == {}

    def test_inline_non_object_degrades_to_empty(self) -> None:
        routes = _load_agent_routes(
            _settings(agent_routes_inline=json.dumps(["a", "b"])),
        )
        assert routes == {}

    def test_yaml_missing_file_degrades_to_empty(self) -> None:
        routes = _load_agent_routes(
            _settings(agent_routes_yaml_path="/nonexistent/path.yaml"),
        )
        assert routes == {}

    def test_yaml_without_agent_routes_section_yields_empty(
        self, tmp_path: Path,
    ) -> None:
        yaml_path = tmp_path / "lite.yaml"
        yaml_path.write_text("model_list: []\n")
        routes = _load_agent_routes(
            _settings(agent_routes_yaml_path=str(yaml_path)),
        )
        assert routes == {}


class TestConstructorOverride:
    def test_constructor_arg_skips_env_load(self) -> None:
        """When `agent_routes=` is passed explicitly, settings env keys
        are ignored — useful for tests that need deterministic routing."""
        client = LLMGatewayClient(
            _settings(agent_routes_inline='{"c3-rag": "from-env"}'),
            agent_routes={"c3-rag": "from-ctor"},
        )
        assert client._agent_routes == {"c3-rag": "from-ctor"}
