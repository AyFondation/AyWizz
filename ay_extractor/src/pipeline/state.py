# src/pipeline/state.py — v2
"""Mutable pipeline state flowing through Phase 1 + Phase 2 agents.

D-020 v1 scope reduction: Phase 3 fields (raw_triplets, entity normalisations,
consolidated_triplets, graph, communities, entity_profiles, relation_profiles,
synthesis, quality_score) were removed alongside the agents that produced
them (concept_extractor, community_summarizer, profile_generator, synthesizer,
critic). The pipeline state is now scoped to extract + chunk + optional
decontext/summarise/densify.

Accumulates results from each phase: extraction, chunking, decontextualization,
summarisation.

See spec §25.1 for original documentation; D-020 R-100-125 for the v1 scope.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ayextractor.core.models import (
    Chunk,
    DocumentStructure,
    ExtractionResult,
    Reference,
)
from ayextractor.pipeline.plugin_kit.models import AgentOutput
from pydantic import BaseModel, Field


class PipelineState(BaseModel):
    """Mutable state accumulating results across Phase 1 + Phase 2.

    Each agent reads from and writes to this state. The pipeline runner
    passes it between agents according to DAG order.
    """

    model_config = {"arbitrary_types_allowed": True}

    # === IDENTITY ===
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    document_title: str = ""
    language: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # === PHASE 1 — EXTRACTION ===
    extraction_result: ExtractionResult | None = None
    enriched_text: str = ""
    structure: DocumentStructure | None = None
    references: list[Reference] = Field(default_factory=list)

    # === PHASE 2 — CHUNKING + DECONTEXTUALIZATION + SUMMARISATION ===
    chunks: list[Chunk] = Field(default_factory=list)
    refine_summary: str = ""
    dense_summary: str = ""

    # === AGENT OUTPUTS (raw) ===
    agent_outputs: dict[str, AgentOutput] = Field(default_factory=dict)

    # === STATS ===
    total_llm_calls: int = 0
    total_tokens_used: int = 0
    # Per-phase observability (R-400-226): one entry per enrichment phase with
    # its wall-clock duration + LLM calls + tokens. Surfaced as
    # `03_metadata/run_stats.json` and in the UI. Dollar cost is sourced
    # separately from the C8 cost receiver (authoritative, snapshot-priced).
    phase_stats: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def record_agent_output(self, agent_name: str, output: AgentOutput) -> None:
        """Record an agent's output and update running stats."""
        self.agent_outputs[agent_name] = output
        self.total_llm_calls += output.metadata.llm_calls
        self.total_tokens_used += output.metadata.tokens_used

    def record_phase(
        self, phase: str, *, duration_ms: int, llm_calls: int = 0, tokens: int = 0
    ) -> None:
        """Append a phase timing/usage entry (best-effort observability)."""
        self.phase_stats.append({
            "phase": phase,
            "duration_ms": duration_ms,
            "llm_calls": llm_calls,
            "tokens": tokens,
        })

    # NOTE: `get_graph_stats` was removed alongside the Phase 3 graph state
    # in the D-020 v1 strip. KG and community statistics will be re-added if
    # Phase 3 is reintroduced (Q-200-022).
