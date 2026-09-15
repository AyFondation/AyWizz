# tests/unit/pipeline/test_unit_llm_factory.py — v2
"""Tests for pipeline/llm_factory.py — D-020 v1 (OpenAI-compat only).

D-020 v1 strip removed all non-OpenAI adapters; the legacy provider-
matching tests (Anthropic/Google/Ollama) are gone. The surviving tests
exercise:
  - the LLMFactory caching contract (one client per resolved assignment),
  - the OpenAI-compat adapter resolution (with C8-style base_url),
  - the unknown-provider error path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ayextractor.config.settings import Settings
from ayextractor.llm.config import LLMAssignment
from ayextractor.pipeline.llm_factory import LLMFactory, _create_client


class TestLLMFactory:
    def test_callable_interface(self):
        """Factory should be callable (for runner.llm_factory)."""
        settings = Settings(_env_file=None, openai_api_key="test-key")
        factory = LLMFactory(settings)
        with patch(
            "ayextractor.pipeline.llm_factory.resolve_llm",
            return_value=LLMAssignment(
                provider="openai",
                model="claude-sonnet-midtier",
                source="default",
            ),
        ), patch(
            "ayextractor.pipeline.llm_factory._create_client",
            return_value=MagicMock(),
        ) as mock_create:
            client = factory("summarizer")
            assert client is not None
            mock_create.assert_called_once()

    def test_caching_same_assignment(self):
        """Same provider:model should return cached client."""
        settings = Settings(_env_file=None, openai_api_key="test-key")
        factory = LLMFactory(settings)
        assignment = LLMAssignment(
            provider="openai", model="claude-sonnet-midtier", source="default",
        )

        with patch(
            "ayextractor.pipeline.llm_factory.resolve_llm",
            return_value=assignment,
        ), patch(
            "ayextractor.pipeline.llm_factory._create_client",
            return_value=MagicMock(),
        ) as mock_create:
            c1 = factory.get_client("summarizer")
            c2 = factory.get_client("densifier")  # same assignment
            assert c1 is c2
            mock_create.assert_called_once()  # only created once

    def test_different_models_separate_clients(self):
        """Different model ids resolve to separate cached clients."""
        settings = Settings(_env_file=None, openai_api_key="k1")
        factory = LLMFactory(settings)

        def mock_resolve(component, _settings):
            if component == "summarizer":
                return LLMAssignment(
                    provider="openai",
                    model="claude-sonnet-midtier",
                    source="default",
                )
            return LLMAssignment(
                provider="openai",
                model="claude-haiku-fast",
                source="component",
            )

        with patch(
            "ayextractor.pipeline.llm_factory.resolve_llm",
            side_effect=mock_resolve,
        ), patch(
            "ayextractor.pipeline.llm_factory._create_client",
            side_effect=lambda a, s, headers=None: MagicMock(name=a.key),
        ):
            c1 = factory.get_client("summarizer")
            c2 = factory.get_client("decontextualizer_screener")
            assert c1 is not c2


class TestCreateClient:
    def test_unknown_provider_raises(self):
        assignment = LLMAssignment(provider="unknown", model="m", source="test")
        settings = Settings(_env_file=None)
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            _create_client(assignment, settings)

    def test_openai_provider(self):
        """OpenAI adapter is constructed with api_key + base_url from settings."""
        assignment = LLMAssignment(
            provider="openai", model="claude-sonnet-midtier", source="test",
        )
        settings = Settings(
            _env_file=None,
            openai_api_key="test-key",
            openai_base_url="http://c8:8000/v1",
        )
        with patch(
            "ayextractor.llm.adapters.openai_adapter.OpenAIAdapter",
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_client(assignment, settings)
            mock_cls.assert_called_once_with(
                api_key="test-key",
                base_url="http://c8:8000/v1",
                model="claude-sonnet-midtier",
                headers=None,
            )

    @pytest.mark.parametrize(
        "legacy_provider",
        ["anthropic", "google", "ollama", "openrouter"],
    )
    def test_legacy_provider_routes_to_openai(self, legacy_provider):
        """D-020 v1: legacy provider strings map to the OpenAI-compat adapter
        — C8 LiteLLM resolves the actual upstream from `agent_routes`."""
        assignment = LLMAssignment(
            provider=legacy_provider, model="some-model", source="test",
        )
        settings = Settings(_env_file=None, openai_api_key="k", openai_base_url="")
        with patch(
            "ayextractor.llm.adapters.openai_adapter.OpenAIAdapter",
        ) as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_client(assignment, settings)
            mock_cls.assert_called_once_with(
                api_key="k",
                base_url=None,
                model="some-model",
                headers=None,
            )
