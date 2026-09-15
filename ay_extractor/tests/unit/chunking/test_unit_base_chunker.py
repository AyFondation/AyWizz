# tests/unit/chunking/test_base_chunker.py — v1
"""Tests for chunking/base_chunker.py — BaseChunker ABC."""

from __future__ import annotations

import pytest

from ayextractor.chunking.base_chunker import BaseChunker


class TestBaseChunker:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseChunker()  # type: ignore[abstract]

    def test_has_required_methods(self):
        assert hasattr(BaseChunker, "strategy_name")
        assert hasattr(BaseChunker, "chunk")
