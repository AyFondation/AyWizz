# tests/unit/llm/test_models.py — v1
"""Tests for llm/models.py — LLM interface types."""

from __future__ import annotations

from ayextractor.llm.models import ImageInput, LLMResponse, Message


class TestMessage:
    def test_user_message(self):
        m = Message(role="user", content="Hello")
        assert m.role == "user"

    def test_all_roles(self):
        for role in ["user", "assistant", "system"]:
            m = Message(role=role, content="test")
            assert m.role == role


class TestImageInput:
    def test_create(self):
        img = ImageInput(data=b"\x89PNG", media_type="image/png")
        assert img.source_id is None

    def test_with_source_id(self):
        img = ImageInput(data=b"data", media_type="image/jpeg", source_id="img_001")
        assert img.source_id == "img_001"


class TestLLMResponse:
    def test_create(self, mock_llm_response):
        assert mock_llm_response.provider == "anthropic"
        assert mock_llm_response.cache_read_tokens == 0

    def test_total_tokens(self):
        r = LLMResponse(
            content="output", input_tokens=100, output_tokens=50,
            model="test", provider="test", latency_ms=100,
        )
        assert r.input_tokens + r.output_tokens == 150
