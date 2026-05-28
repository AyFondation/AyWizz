# =============================================================================
# File: config.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c7_memory/config.py
# Description: Runtime settings for C7 Memory Service.
#
#              v2: env-var single-source refactor (R-100-110 v2, R-100-111
#              v2). Shared infra params read via validation_alias, including
#              the new platform-wide `OLLAMA_URL` (was C7-specific
#              `C7_EMBEDDING_OLLAMA_URL`). Per-component fields keep the
#              `C7_` prefix.
#
#              v3 (Phase F.2): adds KG expansion knobs for hybrid
#              retrieval (`kg_expansion_depth`, `kg_expansion_boost`,
#              `kg_expansion_neighbour_cap`). Active when both `kg_repo`
#              and a populated graph are present at retrieve time;
#              dormant otherwise.
#
# @relation implements:R-100-111
# @relation implements:R-100-110
# @relation implements:R-100-112
# =============================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemoryConfig(BaseSettings):
    """C7 runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="c7_", extra="ignore", populate_by_name=True
    )

    # ---- Platform-wide (read without prefix via validation_alias) -----------
    platform_environment: Literal["development", "testing", "staging", "production"] = (
        Field(default="development", validation_alias="PLATFORM_ENVIRONMENT")
    )

    # Shared ArangoDB
    arango_url: str = Field(
        default="http://arangodb:8529", validation_alias="ARANGO_URL"
    )
    arango_db: str = Field(default="platform", validation_alias="ARANGO_DB")
    arango_username: str = Field(default="ay_app", validation_alias="ARANGO_USERNAME")
    arango_password: str = Field(
        default="changeme", validation_alias="ARANGO_PASSWORD"
    )

    # Shared MinIO
    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="ay_app", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(
        default="changeme", validation_alias="MINIO_SECRET_KEY"
    )
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")

    # Shared Ollama (C7 is the only consumer today, but the URL is platform-
    # level; promoted to a shared knob so a future component reusing Ollama
    # picks up the same value with no duplication.)
    ollama_url: str = Field(
        default="http://ollama:11434", validation_alias="OLLAMA_URL"
    )

    # ---- C7-specific (C7_ prefix) ------------------------------------------
    minio_bucket: str = "memory"

    # Embedding adapter selection.
    #   - "deterministic-hash": zero-dep baseline (reproducible, no ML).
    #   - "ollama": call the running Ollama server at the shared `OLLAMA_URL`
    #     serving `embedding_model_id` (e.g. "all-minilm").
    embedding_adapter: str = "deterministic-hash"
    embedding_model_id: str = "deterministic-hash-v1"
    embedding_dimension: int = Field(default=128, ge=8)
    # The Ollama call timeout is C7-specific (it is THIS component that calls
    # Ollama; another component might tolerate a different latency budget).
    embedding_ollama_timeout_s: float = Field(default=30.0, ge=1.0)

    # Chunking (R-400-022)
    chunk_token_size: int = Field(default=512, ge=16)
    chunk_overlap: int = Field(default=64, ge=0)

    # Per-project storage quota (R-400-024). Default 1 GB.
    default_quota_bytes: int = Field(default=1 * 1024 * 1024 * 1024, ge=0)

    # Retrieval scan cap (R-400-011). Queries that would scan more than
    # this many rows SHALL be filtered down first. Floor lowered to 2
    # in v3 so integration tests for F.2's pool-widening path can
    # construct cut-off scenarios without ingesting hundreds of chunks
    # — production configs always set this in the thousands.
    retrieval_scan_cap: int = Field(default=50_000, ge=2)

    # Hybrid retrieval (R-400-202). `hybrid` (default, spec-mandated) fuses
    # the dense (cosine, R-400-011) and BM25-lexical arms by reciprocal rank
    # fusion ; `dense` keeps the pure-cosine v1 path. `rrf_k` is the RRF
    # constant (higher = flatter rank weighting ; 60 is the common default).
    retrieval_mode: Literal["hybrid", "dense"] = Field(default="hybrid")
    rrf_k: int = Field(default=60, ge=1)

    # Cumulative chunk contextualisation at ingestion (R-400-203). When
    # enabled AND an LLM client is wired, each chunk is augmented with a
    # short document-aware context before embedding (the embedded text is
    # the contextualised chunk). No-ops gracefully without an LLM. The
    # contextualiser is routed via C8 by `contextualisation_agent` (a small
    # Haiku-class model).
    contextualisation_enabled: bool = Field(default=True)
    contextualisation_agent: str = Field(default="c7-contextualizer")

    # Upload limits (Phase B of v1 plan). 50 MiB cap — large enough for
    # most PDFs / DOCX, small enough to keep request budgets bounded.
    # Operators can raise this via env if production needs bigger sources;
    # the chunker still bounds memory at the chunking stage.
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)

    # When True (default), `ingest_uploaded_source` triggers
    # `extract_kg` on the freshly indexed source so the F.2 hybrid
    # retrieval graph is populated without a separate manual call.
    # Only fires when both `kg_repo` and `llm_client` are wired —
    # otherwise this flag is moot. Best-effort: a KG extraction
    # failure SHALL NOT cause the upload to fail.
    #
    # Set to False in environments where:
    #   - The LLM is rate-limited and KG is best-extracted
    #     out-of-band (cron job or NATS worker).
    #   - Upload latency is critical (extract_kg adds ~5-30 s).
    auto_extract_kg_on_upload: bool = True

    # ---- Phase F.2 — KG expansion at retrieve time ------------------------
    # Active iff `kg_repo` is wired AND the project graph has at least
    # one entity touching the seed source_ids. Dormant otherwise.
    #
    # `kg_expansion_depth`: graph hops from seed entities. 1 = direct
    # neighbours only. v1 caps at 1; deeper traversal explodes the
    # candidate pool without proportional precision.
    kg_expansion_depth: int = Field(default=1, ge=1, le=2)
    # `kg_expansion_boost`: multiplicative bonus applied to the cosine
    # score of chunks whose source_id is reachable in the graph from a
    # top-K vector seed. 1.0 = no boost (pool widening only). 1.3 =
    # 30% bump — graph signal nudges borderline candidates without
    # overriding clearly more relevant pure-vector hits.
    kg_expansion_boost: float = Field(default=1.3, ge=1.0, le=3.0)
    # `kg_expansion_neighbour_cap`: max number of NEW source_ids
    # pulled in via graph (cuts the AQL traversal result before fetch).
    # Bounds the cost of the extra fetch + scoring round.
    kg_expansion_neighbour_cap: int = Field(default=20, ge=1, le=200)
