# src/api/models.py — v3
"""API-level models: DocumentInput, Metadata, ConfigOverrides, AnalysisResult.

D-020 v1 scope reduction: `AnalysisResult` no longer carries Phase 3
output (themes, concepts, relations, community_count, graph_path,
communities_path, profiles_path) — those phases were stripped, and the
KG-style outputs are produced by C7 downstream (R-400-200..202) on the
chunks AyExtractor exports. `ConfigOverrides` likewise drops the Phase 3
fields (community_detection_*, profile_generation_*, consolidator_*,
critic_*, entity_similarity_threshold, relation_taxonomy_*). Renamed
`density_iterations` → `chain_of_density_iterations` for clarity.

D-020 session 4 (v3): added `Metadata.tenant_id` / `project_id` /
`source_id` / `quality_tier` / `urgency` (R-400-220 v2 keys + R-400-224
quality tier + R-100-125 v2 §2 urgency flag). `AnalysisResult` gained
`artifact_prefix` (the MinIO scope) + `manifest_key` + `chunks_key` +
`embeddings_key` + `status_key` so the caller (n8n) can read the
artifacts directly without re-deriving paths.

See spec §2.2 for original documentation; D-020 R-100-125 for the v1 scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ConfigOverrides(BaseModel):
    """Per-document overrides — validated subset of Settings (D-020 v1 scope)."""

    # LLM routing (per-agent) — keyed by agent name, value is "provider:model".
    # Under D-020, these flow through C8 LiteLLM and are resolved against
    # `agent_routes` server-side; AyExtractor only forwards the model id.
    llm_assignments: dict[str, str] | None = None

    # Chunking strategy + sizing (Phase 2a — pure Python, no LLM).
    chunking_strategy: str | None = None
    chunk_target_size: int | None = None
    chunk_overlap: int | None = None

    # Phase 2 LLM agents (gated by R-400-224 `quality_tier`).
    decontextualization_enabled: bool | None = None
    summarization_enabled: bool | None = None
    densification_enabled: bool | None = None
    chain_of_density_iterations: int | None = None
    # Image vision captioning + dedup (extraction phase). Its MODEL is chosen
    # independently via `llm_assignments["image_analyzer"]` (a project can run a
    # different model/provider for image analysis than for the text agents).
    image_vision_enabled: bool | None = None

    # Output toggles (used by storage writers in session 3).
    output_format: str | None = None


class DocumentInput(BaseModel):
    """Input document for analysis."""

    content: bytes | str | Path | list[Path]
    format: str
    filename: str


class Metadata(BaseModel):
    """Execution metadata provided by the caller.

    The D-020 C13 fields (`tenant_id`, `project_id`, `source_id`,
    `quality_tier`, `urgency`) are optional from the Python API's
    perspective so the legacy CLI (`ayextractor analyze ...`) keeps
    working; the HTTP wrapper (`api/http.py`) populates them from the
    request body and enforces presence at the boundary.
    """

    # Legacy fields (preserved for CLI / standalone use).
    document_id: str | None = None
    document_type: str = "report"
    output_path: Path = Path("./output")
    language: str | None = None
    resume_from_run: str | None = None
    resume_from_step: int | None = None
    config_overrides: ConfigOverrides | None = None

    # D-020 C13 scoping fields — required when invoking via HTTP /analyze.
    tenant_id: str | None = None
    project_id: str | None = None
    source_id: str | None = None
    quality_tier: Literal["minimal", "standard", "high"] = "minimal"
    urgency: Literal["interactive", "background"] = "interactive"

    # D-020 session 5 — caller-pre-minted run_id (HTTP wrapper). When set,
    # `facade.analyze` SHALL use this run_id verbatim instead of generating
    # its own; ensures `GET /status/{run_id}` and the MinIO `runs/{run_id}`
    # prefix refer to the same artifact set. When None (CLI path) the
    # facade mints internally.
    run_id: str | None = None


class AnalysisResult(BaseModel):
    """Return value of facade.analyze() — API-level result (D-020 v1 scope)."""

    document_id: str
    run_id: str
    summary: str = ""  # dense_summary if quality_tier=high, else refine_summary
    chunks_count: int = 0
    output_dir: Path
    run_dir: Path
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    fingerprint: object = None  # DocumentFingerprint — resolved at runtime
    usage_stats: object = None  # SessionStats — resolved at runtime

    # D-020 v2 — MinIO artifact references (R-400-220 v2 layout).
    # All present only when the analyze call wrote to MinIO (HTTP wrapper).
    # Standalone CLI invocations leave them empty.
    artifact_prefix: str | None = None
    manifest_key: str | None = None
    chunks_key: str | None = None
    embeddings_key: str | None = None
    status_key: str | None = None
