# tests/unit/llm/test_config.py — v1
"""Tests for llm/config.py — per-component LLM routing cascade."""

from __future__ import annotations

from ayextractor.config.settings import Settings
from ayextractor.llm.config import LLMAssignment, resolve_all, resolve_llm


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


class TestResolveLLM:
    def test_fallback_to_default(self):
        # D-020 v1: the default provider is `openai` (OpenAI-compatible adapter
        # pointing at the C8 LiteLLM gateway — all other adapters were stripped).
        s = _settings()
        r = resolve_llm("summarizer", s)
        assert r.provider == "openai"
        assert r.source == "default"

    def test_per_component_override(self):
        s = _settings(llm_summarizer="openai:gpt-4o")
        r = resolve_llm("summarizer", s)
        assert r.provider == "openai"
        assert r.model == "gpt-4o"
        assert r.source == "component"

    def test_per_phase_override(self):
        s = _settings(llm_phase_chunking="google:gemini-pro")
        r = resolve_llm("summarizer", s)
        assert r.provider == "google"
        assert r.model == "gemini-pro"
        assert r.source == "phase"

    def test_component_overrides_phase(self):
        s = _settings(
            llm_phase_chunking="google:gemini-pro",
            llm_summarizer="openai:gpt-4o",
        )
        r = resolve_llm("summarizer", s)
        assert r.provider == "openai"
        assert r.source == "component"

    def test_unknown_component_uses_default(self):
        s = _settings()
        r = resolve_llm("unknown_agent", s)
        assert r.source == "default"

    def test_key_format(self):
        r = LLMAssignment(provider="anthropic", model="claude-sonnet-4-20250514", source="default")
        assert r.key == "anthropic:claude-sonnet-4-20250514"


class TestResolveAll:
    def test_resolves_all_components(self):
        # D-020 v1 strip: `resolve_all` derives from PHASE_COMPONENT_MAP, now
        # {image_analyzer, summarizer, densifier, decontextualizer} — the
        # Phase-3 normalizers (entity_normalizer, …) were removed.
        s = _settings()
        assignments = resolve_all(s)
        assert {"image_analyzer", "summarizer", "densifier", "decontextualizer"} <= set(
            assignments
        )
        assert "entity_normalizer" not in assignments

    def test_no_duplicates(self):
        s = _settings()
        assignments = resolve_all(s)
        assert len(assignments) == len(set(assignments.keys()))
