# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c7_memory/models.py
# Description: Pydantic v2 models for the C7 Memory Service. Mirrors the
#              contract-critical entities E-400-001..005 from
#              400-SPEC-MEMORY-RAG. v2 adds provenance + confidence on KG
#              nodes/edges (R-400-201).
#
# @relation implements:R-400-010
# @relation implements:R-400-040
# @relation implements:R-400-201
# @relation implements:E-400-002
# @relation implements:E-400-003
# =============================================================================

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class IndexKind(StrEnum):
    """Logical indexes per R-400-010 / D-013.

    v2 (Phase E of v1 plan) adds CONVERSATIONS — chunks fed back from
    C3 chat exchanges. Federated retrieval can target any subset; the
    chat-with-RAG flow queries `EXTERNAL_SOURCES + CONVERSATIONS` so
    follow-up questions benefit both from uploaded sources and from
    the conversation's own prior turns.
    """

    REQUIREMENTS = "requirements"
    EXTERNAL_SOURCES = "external_sources"
    CONVERSATIONS = "conversations"


class ChunkStatus(StrEnum):
    """Status of an embedded chunk — used for retrieval filtering."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


class ParseStatus(StrEnum):
    """Ingestion pipeline status on a source (R-400-020)."""

    PENDING = "pending"
    PARSED = "parsed"
    INDEXED = "indexed"
    FAILED = "failed"


class RefreshJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Public chunk / source views (wire-level)
# ---------------------------------------------------------------------------


class ChunkPublic(BaseModel):
    """A single retrievable chunk — returned by the retriever."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    project_id: str
    index: IndexKind
    source_id: str | None = None
    entity_id: str | None = None
    entity_version: int | None = None
    chunk_index: int
    content: str
    content_hash: str
    model_id: str
    model_dim: int
    created_at: datetime
    status: ChunkStatus
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourcePublic(BaseModel):
    """Metadata about an uploaded external source (E-400-003)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    project_id: str
    mime_type: str
    size_bytes: int
    uploaded_by: str
    uploaded_at: datetime
    parse_status: ParseStatus
    parse_error: str | None = None
    chunk_count: int
    model_id: str | None = None

    processing_version: str | None = None
    """Descriptor of the pipeline that produced this source's chunks
    (R-400-208) — chunk window/overlap + embedding model. None on legacy
    sources ingested before versioning."""
    is_stale: bool = False
    """True when `processing_version` differs from the pipeline's current
    version, i.e. a `reprocess` would change the result (R-400-208)."""


# ---------------------------------------------------------------------------
# Retrieval surface (R-400-040)
# ---------------------------------------------------------------------------


class RetrievalRequest(BaseModel):
    """POST /api/v1/memory/retrieve body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    query: str = Field(min_length=1)
    indexes: list[IndexKind] = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=50)
    weights: dict[IndexKind, float] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    include_history: bool = False
    include_deprecated: bool = False


class RetrievalHit(BaseModel):
    """One hit in a retrieval response (includes score + provenance)."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    index: IndexKind
    score: float
    content: str
    snippet: str
    source_id: str | None = None
    entity_id: str | None = None
    entity_version: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    """Federated retrieval response (R-400-040)."""

    model_config = ConfigDict(extra="forbid")

    retrieval_id: str
    request: RetrievalRequest
    hits: list[RetrievalHit] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Ingestion entry point (v1 has C7 receive *parsed* payloads — uploads live
# on C12, see R-400-020). This model is used both for NATS payloads and for
# the admin-only direct-ingest endpoint used by tests and operators.
# ---------------------------------------------------------------------------


class SourceIngestRequest(BaseModel):
    """Direct ingestion of an already-parsed source (admin/test surface).

    `mime_type` SHALL match one of the parsers registered in
    `c7_memory/ingestion/parser.py`. v1 (Phase B) supports text/plain,
    text/markdown, text/html, application/pdf, and the OpenXML DOCX
    MIME. Image OCR (image/png, image/jpeg) is NOT supported in v1 —
    those types were declared in v0 but never wired and are removed
    from the contract surface.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    project_id: str
    mime_type: Literal[
        "text/plain",
        "text/markdown",
        "text/html",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    content: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    uploaded_by: str


# ---------------------------------------------------------------------------
# C13 (AyExtractor) ingest-chunks surface (R-400-222 v2 / R-400-223 v2)
# ---------------------------------------------------------------------------


class ChunkRich(BaseModel):
    """One chunk produced by C13 (AyExtractor) — R-400-222 v2 shape.

    The rich shape carries every Phase 2 output downstream so retrieval
    can present chunks with their local context without an extra LLM
    call at query time. `embedding` is populated by C13 (D-020 v2 §B1);
    when absent, C7 falls back to its own embedder (transitional, removed
    in session 7 per D-020 roadmap).
    """

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    seq: int = Field(ge=0)
    text: str = Field(min_length=1)
    """The text C7 SHALL embed (= decontextualised variant if
    quality_tier=high AND screener returned YES, else == original_text)."""
    original_text: str | None = None
    """Pre-decontextualisation text — present only when `text != original`."""
    context_summary: str | None = None
    """Cumulative Refine summary up to and including this chunk
    (quality_tier in {standard, high})."""
    global_summary: str | None = None
    """Chain of Density output for the whole document, duplicated across
    all chunks (quality_tier=high)."""
    section_path: list[str] = Field(default_factory=list)
    char_start: int = Field(ge=0, default=0)
    char_end: int = Field(ge=0, default=0)
    token_count: int = Field(ge=0, default=0)
    references: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    """Image content hashes (sha8) cited inline in this chunk."""
    tables: list[str] = Field(default_factory=list)
    extraction_run_id: str
    """= RunManifest.run_id (R-400-221 v2)."""

    # --- Embedding (D-020 v2 §B1) ---
    embedding: list[float] | None = None
    """Vector of `embedding_dimension` floats produced by C13. SHOULD be
    present; when absent, C7 falls back to its own embedder
    (transitional, removed v2 per D-020 session 7)."""


class ChunkIngestRequest(BaseModel):
    """POST /memory/projects/{pid}/sources/{sid}/ingest-chunks body
    (R-400-223 v2 pure-INSERT path).

    Body fields mirror the R-400-220 v2 artifact set produced by C13.
    `embedding_model` / `embedding_dimension` are cross-validated
    against the `embedding` field of each `ChunkRich` so a partial
    upload corrupts neither the index nor the manifest.
    """

    model_config = ConfigDict(extra="forbid")

    extraction_run_id: str = Field(min_length=1)
    """RunManifest.run_id stamped by C13."""
    manifest_object_key: str | None = None
    """MinIO key of the run_manifest.json — diagnostic reference only;
    C7 does NOT re-read it (the request body is authoritative)."""
    embedding_model: str = Field(min_length=1)
    """Model id reported by C13 (must equal `ChunkRich.embedding` shape)."""
    embedding_model_version: str = ""
    """Optional version pin from the provider; empty string acceptable."""
    embedding_dimension: int = Field(ge=1)
    chunks: list[ChunkRich] = Field(min_length=1)
    uploaded_by: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)


class EntityEmbedRequest(BaseModel):
    """Event-driven request to (re)embed a C5 entity (R-400-030)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    entity_id: str
    entity_version: int
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # When true, prior versions of this entity remain as `superseded` in the
    # index (R-400-031). When false, they are deleted.
    preserve_history: bool = True


# ---------------------------------------------------------------------------
# Refresh jobs (R-400-061)
# ---------------------------------------------------------------------------


class RefreshJob(BaseModel):
    """Admin refresh job descriptor."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    project_id: str
    status: RefreshJobStatus
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    processed_chunks: int = 0
    total_chunks: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


class QuotaStatus(BaseModel):
    """GET /api/v1/memory/projects/{pid}/quota response (R-400-024)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    bytes_used: int
    bytes_limit: int
    chunk_count: int
    source_count: int


# ---------------------------------------------------------------------------
# List envelopes
# ---------------------------------------------------------------------------


class SourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourcePublic]


class ChunkListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunks: list[ChunkPublic]


# ---------------------------------------------------------------------------
# Phase F (v1.1) — Knowledge graph extraction
# ---------------------------------------------------------------------------


class Provenance(StrEnum):
    """Whether a knowledge node/edge was deterministically extracted or
    LLM-inferred (R-400-201).

    Provenance is the basis for the future lint/audit pass and for honest
    reporting to auditors: an `EXTRACTED` fact (e.g. an AST symbol) is not
    the same epistemic object as an `INFERRED` one (an LLM-derived
    relation). The default everywhere is `INFERRED` (the safe assumption);
    a deterministic structural extractor sets `EXTRACTED` explicitly.
    """

    EXTRACTED = "extracted"
    INFERRED = "inferred"


class KGEntity(BaseModel):
    """One entity extracted from a source.

    Persisted in `memory_kg_entities` (vertex collection). Identified
    by composite key `(tenant_id, project_id, name, type)`; multiple
    sources mentioning the same entity converge on the same vertex.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    type: str = Field(min_length=1, max_length=64)
    """Free-form entity type (e.g. "person", "place", "organization",
    "concept"). The LLM is prompted to use a small canonical set but
    we accept whatever it returns — no enum constraint in v1."""

    provenance: Provenance = Field(default=Provenance.INFERRED)
    """How this node was obtained (R-400-201). The C7 source extractor is
    LLM-based, so its nodes default to INFERRED; a deterministic structural
    extractor (R-400-200, future component) sets EXTRACTED."""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """Extraction confidence in [0,1]. EXTRACTED records are 1.0; INFERRED
    records carry the model-reported or calibrated score — v1 defaults to
    1.0 (face value) until the extraction prompt reports per-item scores."""


class KGRelation(BaseModel):
    """One directed relation between two entities, derived by the LLM.

    Persisted in `memory_kg_relations` (Arango edge collection)
    pointing from the subject vertex to the object vertex.
    """

    model_config = ConfigDict(extra="forbid")

    subject: KGEntity
    relation: str = Field(min_length=1, max_length=80)
    object: KGEntity

    provenance: Provenance = Field(default=Provenance.INFERRED)
    """How this edge was obtained (R-400-201). LLM-derived by default;
    a deterministic extractor sets EXTRACTED."""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """Edge extraction confidence in [0,1]. See KGEntity.confidence."""


class KGExtractionResult(BaseModel):
    """Response shape of POST /sources/{sid}/extract-kg.

    Both lists may be empty when the source contains no nameable
    entities; that's a legitimate outcome, not an error."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    entities_added: int
    relations_added: int
    entities: list[KGEntity] = Field(default_factory=list)
    relations: list[KGRelation] = Field(default_factory=list)


class KGRelationSample(BaseModel):
    """One triple in the KG summary view, carrying its provenance."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    relation: str
    object: str
    provenance: Provenance = Provenance.INFERRED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class KGSummary(BaseModel):
    """Lightweight inspection view of a project's knowledge graph (the
    'graph bootstrap'): entity/relation counts + a small sample of triples
    with provenance. Read-only; no graph mutation."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    entity_count: int
    relation_count: int
    sample: list[KGRelationSample] = Field(default_factory=list)
