# src/config/settings.py — v2
"""Typed configuration loaded from .env via pydantic-settings.

D-020 v1 strip: removed per-provider API keys (anthropic, google, openrouter)
and multi-vector/graph DB configuration. Only OpenAI-compatible routing
(pointing at C8 LiteLLM via `openai_base_url`) survives. The cache
backend collapsed to `json` only; the output writer collapsed to `minio`
only (see writer_factory v3 + cache_factory v3).

Single source of truth for all deployment-specific settings.
See spec §17.1 for original layout; D-020 R-100-125 for v1 scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(Exception):
    """Raised when configuration is internally inconsistent (spec §17.6)."""


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === LLM ROUTING (D-020 v1: OpenAI-compatible only, points at C8) ===
    llm_default_provider: str = "openai"
    llm_default_model: str = "claude-sonnet-midtier"   # resolved by C8 agent_routes
    llm_default_temperature: float = 0.2
    llm_max_tokens_per_agent: int = 4096

    # OpenAI-compatible endpoint. In-cluster: C8 (http://c8:8000/v1).
    # Standalone dev: any OpenAI-API-compatible URL (Anthropic /v1/openai,
    # Ollama /v1, …).
    openai_api_key: str = ""
    openai_base_url: str = "http://c8:8000/v1"

    # Legacy provider keys kept ONLY for backward-compat with .env files
    # in dev environments — not consumed by D-020 v1 code paths.
    anthropic_api_key: str = ""
    google_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_app_name: str = "ayExtractor"

    # Per-phase LLM assignment (D-020 v1: only extraction + chunking remain).
    llm_phase_extraction: str = ""
    llm_phase_chunking: str = ""

    # Per-component LLM assignment (highest priority — D-020 v1 surviving agents).
    llm_image_analyzer: str = ""
    llm_summarizer: str = ""
    llm_densifier: str = ""
    llm_decontextualizer: str = ""
    # D-020 v2 §A — Haiku-class screener gating the decontextualiser.
    llm_decontextualizer_screener: str = ""

    # === EMBEDDINGS (legacy adapter knobs — D-020 v1 strip removed the
    #     per-provider embedders. These fields stay as no-ops so existing
    #     .env files don't fail; the active embedding config lives below
    #     under "Embeddings (D-020 v2 §B1)".) ===
    embedding_provider: str = "anthropic"
    embedding_dimensions: int = 1024
    embedding_ollama_model: str = "nomic-embed-text"
    embedding_st_model: str = "all-MiniLM-L6-v2"

    # === Document limits ===
    max_document_size_mb: int = 50
    max_document_pages: int = 2000
    max_document_tokens: int = 500_000
    oversize_strategy: Literal["reject", "truncate", "sample"] = "reject"

    # === Chunking ===
    chunking_strategy: Literal["structural", "semantic"] = "structural"
    chunk_target_size: int = 2000
    chunk_overlap: int = 0
    decontextualization_enabled: bool = True
    decontextualizer_tool_use: Literal["auto", "always", "never"] = "auto"
    decontextualizer_tool_confidence_threshold: float = 0.7

    # === Pipeline (D-020 v1 scope — Phase 1+2 only) ===
    density_iterations: int = 5
    confidence_threshold: float = 0.6
    # D-020 v2 §B1 — embeddings computed at C13 via C8 /embeddings.
    embeddings_at_extractor: bool = True
    # D-020 v2 §C1 — opt-in batch API mode.
    urgency: Literal["interactive", "background"] = "interactive"

    # === Cache (D-020 v1: only json store survives, backed by MinIO when
    #            deployed as C13) ===
    cache_enabled: bool = True
    cache_backend: Literal["json"] = "json"
    cache_root: Path = Path("~/.ayextractor/cache")
    simhash_threshold: int = 3
    minhash_threshold: float = 0.8
    constellation_threshold: float = 0.7

    # === Output storage (D-020 session 3: MinIO writer wired) ===
    output_writer: Literal["minio"] = "minio"
    output_minio_bucket: str = "c13-extractor-artifacts"
    output_minio_prefix: str = ""
    output_minio_endpoint: str = ""
    # MinIO credentials — dedicated runtime user (R-100-118). When empty,
    # boto3 falls back to its default chain (AWS_* env vars, IAM role, …).
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_region: str = "us-east-1"

    # === Embeddings (D-020 v2 §B1 — computed by C13 via C8 /embeddings) ===
    # Model id resolved C8-side via `agent_routes` (e.g. `voyage-3`).
    embedding_model: str = "voyage-3"
    # Per-batch size — most providers cap at 100-2048. Conservative default.
    embedding_batch_size: int = 100
    # Embeddings egress is INDEPENDENT of the chat egress: the chat LLMs route
    # through C8 LiteLLM (Claude), which carries NO embeddings model, so the
    # embeddings endpoint may point elsewhere (e.g. a local Ollama in dev, or a
    # Voyage/OpenAI endpoint in prod). When empty, fall back to `openai_*`.
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    batch_scan_formats: str = "pdf,epub,docx,md,txt,png,jpg,jpeg,webp"

    # === Output ===
    output_format: Literal["markdown", "json", "both"] = "both"

    # === Logging ===
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    log_file: Path = Path("~/.ayextractor/logs/analyzer.log")
    log_rotation: str = "10MB"
    log_retention: int = 30

    # === Vector database ===
    vector_db_type: Literal["none", "chromadb", "qdrant", "arangodb"] = "none"
    vector_db_path: Path = Path("~/.ayextractor/vectordb")
    vector_db_url: str = ""
    vector_db_api_key: str = ""
    vector_db_collection: str = "ayextractor"

    # === Graph database ===
    graph_db_type: Literal["none", "neo4j", "arangodb"] = "none"
    graph_db_uri: str = "bolt://localhost:7687"
    graph_db_database: str = "ayextractor"
    graph_db_user: str = ""
    graph_db_password: str = ""
    graph_db_merge_strategy: Literal["incremental", "replace"] = "incremental"

    # === RAG ===
    # D-020 v1 strip: rag_*, consolidator_*, gpu_*, chunk_output_mode,
    # vector_db_type, graph_db_type, graph_export_formats, batch_scan_*
    # settings were removed alongside the modules that consumed them.
    # The platform's C7 owns RAG enrichment / vector+graph indexing.

    # --- Validators ---

    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, v: int, info) -> int:  # noqa: N805
        """V-05: CHUNK_OVERLAP must be non-negative."""
        if v < 0:
            raise ValueError("chunk_overlap must be >= 0")
        return v

    @model_validator(mode="after")
    def validate_config_consistency(self) -> Settings:
        """Validate cross-field consistency rules (D-020 v1 subset).

        Removed validators (no longer relevant after D-020 strip):
          V-01/V-02 — `chunk_output_mode` vs `vector_db_type` / `graph_db_type`
          V-03 — `rag_enabled` requires a DB
          V-04 — `consolidator_enabled` requires a graph DB
        """
        errors: list[str] = []

        # V-05
        if self.chunk_overlap >= self.chunk_target_size:
            errors.append("CHUNK_OVERLAP must be < CHUNK_TARGET_SIZE")

        if errors:
            raise ConfigurationError("; ".join(errors))

        return self


def load_settings(**overrides: object) -> Settings:
    """Load settings from .env with optional overrides.

    Args:
        **overrides: Field-level overrides (for testing or per-document config).

    Returns:
        Validated Settings instance.

    Raises:
        ConfigurationError: If configuration is internally inconsistent.
    """
    return Settings(**overrides)  # type: ignore[arg-type]
