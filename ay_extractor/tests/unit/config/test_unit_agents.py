# tests/unit/config/test_agents.py — v1
"""Tests for config/agents.py — agent registry and phase mapping."""

from __future__ import annotations

from ayextractor.config.agents import AGENT_REGISTRY, PHASE_COMPONENT_MAP


class TestAgentRegistry:
    def test_registry_not_empty(self):
        assert len(AGENT_REGISTRY) > 0

    def test_all_entries_are_fqcn(self):
        for entry in AGENT_REGISTRY:
            assert "." in entry, f"Entry should be a fully qualified class path: {entry}"
            assert entry.startswith("ayextractor.")

    def test_decontextualizer_not_in_registry(self):
        for entry in AGENT_REGISTRY:
            assert "decontextualizer" not in entry.lower()

    def test_known_agents_present(self):
        # D-020 v1 strip: only the two Phase-2 DAG agents survive
        # (ConceptExtractor / Synthesizer + the rest of Phase 3 were removed).
        names = [e.split(".")[-1] for e in AGENT_REGISTRY]
        assert "SummarizerAgent" in names
        assert "DensifierAgent" in names
        assert "ConceptExtractorAgent" not in names
        assert "SynthesizerAgent" not in names


class TestPhaseComponentMap:
    def test_phases(self):
        # D-020 v1 strip: Phase-3 phases (analysis, normalization) were removed.
        assert set(PHASE_COMPONENT_MAP.keys()) == {"extraction", "chunking"}

    def test_no_duplicate_components(self):
        all_components = []
        for components in PHASE_COMPONENT_MAP.values():
            all_components.extend(components)
        assert len(all_components) == len(set(all_components))

    def test_summarizer_in_chunking(self):
        assert "summarizer" in PHASE_COMPONENT_MAP["chunking"]
