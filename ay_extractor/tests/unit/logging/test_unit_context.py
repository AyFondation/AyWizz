# tests/unit/logging/test_context.py — v1
"""Tests for logging/context.py — contextual logging variables."""

from __future__ import annotations

from ayextractor.logging.context import (
    clear_context,
    get_context,
    set_agent_context,
    set_document_context,
)


class TestLogContext:
    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    def test_initial_state(self):
        ctx = get_context()
        assert ctx.document_id is None
        assert ctx.run_id is None
        assert ctx.agent is None

    def test_set_document_context(self):
        set_document_context("doc1", "run1")
        ctx = get_context()
        assert ctx.document_id == "doc1"
        assert ctx.run_id == "run1"

    def test_set_agent_context(self):
        set_agent_context("summarizer", "refine_chunk_003")
        ctx = get_context()
        assert ctx.agent == "summarizer"
        assert ctx.step == "refine_chunk_003"

    def test_as_dict_filters_none(self):
        set_document_context("doc1", "run1")
        ctx = get_context()
        d = ctx.as_dict()
        assert "document_id" in d
        assert "agent" not in d

    def test_clear(self):
        set_document_context("doc1", "run1")
        set_agent_context("test")
        clear_context()
        ctx = get_context()
        assert ctx.document_id is None
        assert ctx.agent is None
