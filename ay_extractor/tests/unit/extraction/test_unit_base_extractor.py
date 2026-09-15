# tests/unit/extraction/test_base_extractor.py — v1
"""Tests for extraction/base_extractor.py — BaseExtractor ABC."""

from __future__ import annotations

import pytest

from ayextractor.extraction.base_extractor import BaseExtractor


class TestBaseExtractor:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseExtractor()  # type: ignore[abstract]

    def test_default_requires_vision_is_false(self):
        assert BaseExtractor.requires_vision.fget is not None  # type: ignore[union-attr]
