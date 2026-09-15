# src/core/models.py — v1
"""Shared Pydantic domain models used across modules.

No module redefines these types — all imports come from core.models.
See spec §3.2 for full documentation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# === SOURCE TRACEABILITY ===


class SourceProvenance(BaseModel):
    """Tracks exactly where a node or edge was extracted from (spec §13.9.6)."""

    document_id: str
    run_id: str
    chunk_ids: list[str]
    context_sentences: list[str]
    first_seen_at: datetime
    extraction_confidence: float


class TemporalScope(BaseModel):
    """When a fact is true — content temporality, NOT ingestion temporality (spec §13.9.7)."""

    type: Literal["point", "range", "recurring"]
    start: str | None = None
    end: str | None = None
    granularity: Literal["day", "month", "quarter", "year", "decade"] | None = None
    raw_expression: str


# === CHUNK MODELS ===


class ChunkSourceSection(BaseModel):
    """Section reference for chunk traceability."""

    title: str
    level: int


class ResolvedReference(BaseModel):
    """A single resolved ambiguous reference within a chunk."""

    original_text: str
    resolved_text: str
    reference_type: Literal["pronoun", "definite_article", "acronym", "implicit_ref"]
    resolution_source: Literal["preceding_chunk", "document_title", "toc", "rag_lookup"]
    position_in_chunk: int


class ChunkDecontextualization(BaseModel):
    """Decontextualization metadata for a chunk."""

    applied: bool
    resolved_references: list[ResolvedReference]
    context_window_size: int
    confidence: float


class Chunk(BaseModel):
    """Document chunk — atomic unit of analysis."""

    # --- Identity ---
    id: str
    position: int
    preceding_chunk_id: str | None = None
    following_chunk_id: str | None = None

    # --- Content ---
    content: str
    original_content: str | None = None
    content_type: Literal["text", "mixed"] = "text"
    embedded_images: list[str] = Field(default_factory=list)
    embedded_tables: list[str] = Field(default_factory=list)

    # --- Source traceability ---
    source_file: str
    source_pages: list[int] = Field(default_factory=list)
    source_sections: list[ChunkSourceSection] = Field(default_factory=list)
    byte_offset_start: int = 0
    byte_offset_end: int = 0

    # --- Metrics ---
    char_count: int = 0
    word_count: int = 0
    token_count_est: int = 0
    overlap_with_previous: int = 0
    fingerprint: str = ""

    # --- Language ---
    primary_language: str = "en"
    secondary_languages: list[str] = Field(default_factory=list)
    is_multilingual: bool = False

    # --- Decontextualization ---
    decontextualization: ChunkDecontextualization | None = None

    # --- Embedding (null in JSON files; set in-memory during vector DB indexation) ---
    embedding: list[float] | None = None
    embedding_model: str | None = None

    # --- Context for disambiguation ---
    context_summary: str | None = None
    global_summary: str | None = None
    key_entities: list[str] = Field(default_factory=list)
    acronyms_expanded: dict[str, str] = Field(default_factory=dict)


# === TRIPLET MODELS ===


class QualifiedTriplet(BaseModel):
    """Triplet extracted from a single chunk, before normalization."""

    subject: str
    predicate: str
    object: str
    source_chunk_id: str
    confidence: float
    context_sentence: str
    qualifiers: dict[str, str] | None = None
    temporal_scope: TemporalScope | None = None


class ConsolidatedTriplet(BaseModel):
    """Normalized and consolidated triplet after inter-chunk merging."""

    subject: str
    predicate: str
    object: str
    source_chunk_ids: list[str]
    occurrence_count: int
    confidence: float
    original_forms: list[str] = Field(default_factory=list)
    qualifiers: dict[str, str | list[str]] | None = None
    temporal_scope: TemporalScope | None = None
    context_sentences: list[str] = Field(default_factory=list)


class EntityNormalization(BaseModel):
    """Entity normalization table entry (nodes)."""

    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: (
        Literal[
            "person",
            "organization",
            "concept",
            "location",
            "document",
            "technology",
        ]
        | None
    ) = None
    occurrence_count: int = 0
    source_chunk_ids: list[str] = Field(default_factory=list)


class RelationTaxonomyEntry(BaseModel):
    """Mapping of a raw relation to the canonical taxonomy."""

    canonical_relation: str
    original_forms: list[str] = Field(default_factory=list)
    category: str
    is_directional: bool = True


# === DOCUMENT STRUCTURE ===


class Section(BaseModel):
    """Detected document section."""

    title: str
    level: int
    start_position: int
    end_position: int


class Footnote(BaseModel):
    """Detected footnote."""

    id: str
    content: str
    position: int


class DocumentStructure(BaseModel):
    """Detected document structure."""

    has_toc: bool = False
    sections: list[Section] = Field(default_factory=list)
    has_bibliography: bool = False
    bibliography_position: int | None = None
    has_annexes: bool = False
    annexes: list[Section] = Field(default_factory=list)
    footnotes: list[Footnote] = Field(default_factory=list)
    has_index: bool = False


class Reference(BaseModel):
    """Cross-reference or citation extracted from document."""

    type: Literal["citation", "footnote", "bibliography", "internal_ref"]
    text: str
    target: str | None = None
    source_chunk_id: str


# === EXTRACTION MODELS ===


class ImageAnalysis(BaseModel):
    """Result of LLM Vision analysis on an embedded image."""

    id: str
    type: Literal[
        "diagram", "chart", "table_image", "photo", "screenshot", "decorative"
    ]
    description: str
    entities: list[str] = Field(default_factory=list)
    source_page: int | None = None


class TableData(BaseModel):
    """Structured table extracted from document."""

    id: str
    content_markdown: str
    source_page: int | None = None
    origin: Literal["structured", "image"]


class ExtractionResult(BaseModel):
    """Complete extraction output from a document."""

    raw_text: str
    enriched_text: str
    images: list[ImageAnalysis] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    structure: DocumentStructure = Field(default_factory=DocumentStructure)
    language: str = "en"


# === API VIEW MODELS ===


class Theme(BaseModel):
    """API view — produced by Synthesizer structured output (step 3g)."""

    name: str
    description: str
    relevance_score: float


class Concept(BaseModel):
    """API view — derived from EntityNormalization during Finalization (step 4)."""

    name: str
    description: str
    aliases: list[str] = Field(default_factory=list)


class Relation(BaseModel):
    """API view — derived from ConsolidatedTriplet during Finalization (step 4)."""

    source: str
    relation_type: str
    target: str
    weight: float


# === UTILITY MODELS ===


class TokenBudget(BaseModel):
    """Token budget tracking across agents."""

    total_estimated: int
    per_agent: dict[str, int] = Field(default_factory=dict)
    consumed: dict[str, int] = Field(default_factory=dict)


class SourceMetadata(BaseModel):
    """Metadata about the original source document."""

    original_filename: str
    format: str
    size_bytes: int
    sha256: str
    stored_at: datetime
