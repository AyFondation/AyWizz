# src/config/agents.py — v2
"""Declarative agent registry configuration.

D-020 v1 scope reduction: only the Phase 2 LLM agents survive
(summarizer, densifier). The Phase 3 agents (concept_extractor,
community_summarizer, profile_generator, synthesizer, critic) and the
LLM-driven reference_extractor were stripped. The decontextualizer
runs in Phase 2 pre-processing and is not listed here.

See spec §25.4 for original layout; D-020 R-100-125 for v1 scope.
"""

from __future__ import annotations

# Fully qualified class paths for dynamic import by plugin_kit/registry.py.
AGENT_REGISTRY: list[str] = [
    "ayextractor.pipeline.agents.summarizer.SummarizerAgent",
    "ayextractor.pipeline.agents.densifier.DensifierAgent",
]

# Phase-to-component mapping for LLM routing (spec §17.3).
# D-020 v1: only `extraction` (image vision) + `chunking` (decontext + summary
# + densify) phases survive. `analysis` (Phase 3) and `normalization` (KG)
# entries removed.
PHASE_COMPONENT_MAP: dict[str, list[str]] = {
    "extraction": ["image_analyzer"],
    "chunking": ["summarizer", "densifier", "decontextualizer"],
}
