# tests/unit/llm/test_base_client.py — v1
"""Tests for llm/base_client.py — BaseLLMClient ABC is not instantiable."""

from __future__ import annotations

import pytest

from ayextractor.llm.base_client import BaseLLMClient


class TestBaseLLMClient:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseLLMClient()  # type: ignore[abstract]

    def test_has_required_methods(self):
        assert hasattr(BaseLLMClient, "complete")
        assert hasattr(BaseLLMClient, "complete_with_vision")
        assert hasattr(BaseLLMClient, "supports_vision")
        assert hasattr(BaseLLMClient, "provider_name")
