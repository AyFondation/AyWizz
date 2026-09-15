# tests/unit/extraction/test_image_analyzer.py — v2
"""Tests for extraction/image_analyzer.py — unit tests with mocked LLM."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from ayextractor.extraction.image_analyzer import analyze_image
from ayextractor.llm.models import LLMResponse


class TestAnalyzeImage:
    @pytest.mark.asyncio
    async def test_basic_call(self, mock_llm_client):
        mock_llm_client.complete_with_vision = AsyncMock(
            return_value=LLMResponse(
                content='{"type": "diagram", "description": "System architecture"}',
                input_tokens=500, output_tokens=50,
                model="claude-sonnet-4-20250514", provider="anthropic", latency_ms=1000,
            )
        )
        result = await analyze_image(
            image_data=b"\x89PNG_FAKE",
            media_type="image/png",
            image_id="img_001",
            llm=mock_llm_client,
        )
        assert result is not None
        mock_llm_client.complete_with_vision.assert_called_once()
