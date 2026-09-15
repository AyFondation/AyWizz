# tests/conftest.py — v1
"""Shared test fixtures for all unit and integration tests.

Provides mock LLM clients, sample chunks, minimal graphs, and temp directories.
No external dependencies — all I/O is mocked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ayextractor.core.models import (
    Chunk,
    ChunkDecontextualization,
    ChunkSourceSection,
    ConsolidatedTriplet,
    DocumentStructure,
    EntityNormalization,
    ExtractionResult,
    QualifiedTriplet,
    ResolvedReference,
    Section,
    SourceProvenance,
    TemporalScope,
)
from ayextractor.llm.models import LLMResponse


# === FIXTURES: Sample data ===


@pytest.fixture
def sample_chunk() -> Chunk:
    """Minimal valid Chunk for testing."""
    return Chunk(
        id="chunk_001",
        position=0,
        content="The European Union regulates cybersecurity through the NIS2 Directive.",
        source_file="report.pdf",
        source_pages=[1],
        source_sections=[ChunkSourceSection(title="1. Introduction", level=1)],
        char_count=70,
        word_count=10,
        token_count_est=15,
        fingerprint="abc123",
        primary_language="en",
        key_entities=["European Union", "NIS2 Directive"],
    )


@pytest.fixture
def sample_chunk_list(sample_chunk: Chunk) -> list[Chunk]:
    """List of 3 linked chunks."""
    c1 = sample_chunk.model_copy(
        update={"id": "chunk_001", "position": 0, "following_chunk_id": "chunk_002"}
    )
    c2 = sample_chunk.model_copy(
        update={
            "id": "chunk_002",
            "position": 1,
            "preceding_chunk_id": "chunk_001",
            "following_chunk_id": "chunk_003",
            "content": "The directive applies to essential and important entities.",
        }
    )
    c3 = sample_chunk.model_copy(
        update={
            "id": "chunk_003",
            "position": 2,
            "preceding_chunk_id": "chunk_002",
            "content": "Member states must transpose it into national law by October 2024.",
        }
    )
    return [c1, c2, c3]


@pytest.fixture
def sample_triplet() -> QualifiedTriplet:
    """Minimal valid QualifiedTriplet."""
    return QualifiedTriplet(
        subject="European Union",
        predicate="regulates",
        object="cybersecurity",
        source_chunk_id="chunk_001",
        confidence=0.9,
        context_sentence="The EU regulates cybersecurity through the NIS2 Directive.",
    )


@pytest.fixture
def sample_consolidated_triplet() -> ConsolidatedTriplet:
    """Minimal valid ConsolidatedTriplet."""
    return ConsolidatedTriplet(
        subject="European Union",
        predicate="regulates",
        object="cybersecurity",
        source_chunk_ids=["chunk_001", "chunk_003"],
        occurrence_count=2,
        confidence=0.92,
        original_forms=["regulates", "réglemente"],
        context_sentences=[
            "The EU regulates cybersecurity.",
            "L'UE réglemente la cybersécurité.",
        ],
    )


@pytest.fixture
def sample_entity_normalization() -> EntityNormalization:
    """Minimal valid EntityNormalization."""
    return EntityNormalization(
        canonical_name="European Union",
        aliases=["EU", "l'UE", "Union européenne"],
        entity_type="organization",
        occurrence_count=5,
        source_chunk_ids=["chunk_001", "chunk_002", "chunk_003"],
    )


@pytest.fixture
def sample_extraction_result() -> ExtractionResult:
    """Minimal valid ExtractionResult."""
    return ExtractionResult(
        raw_text="Raw document text content.",
        enriched_text="Enriched document text content with markers.",
        structure=DocumentStructure(
            has_toc=True,
            sections=[Section(title="Introduction", level=1, start_position=0, end_position=100)],
        ),
        language="en",
    )


@pytest.fixture
def sample_provenance() -> SourceProvenance:
    """Minimal valid SourceProvenance."""
    return SourceProvenance(
        document_id="20260207_140000_a1b2c3d4",
        run_id="20260207_1615_b3c8d",
        chunk_ids=["chunk_001"],
        context_sentences=["The EU regulates cybersecurity."],
        first_seen_at=datetime(2026, 2, 7, 14, 0, 0, tzinfo=timezone.utc),
        extraction_confidence=0.9,
    )


@pytest.fixture
def sample_temporal_scope() -> TemporalScope:
    """Minimal valid TemporalScope."""
    return TemporalScope(
        type="range",
        start="2024-01",
        end="2025-12",
        granularity="month",
        raw_expression="from January 2024 to December 2025",
    )


@pytest.fixture
def sample_decontextualization() -> ChunkDecontextualization:
    """Minimal valid ChunkDecontextualization."""
    return ChunkDecontextualization(
        applied=True,
        resolved_references=[
            ResolvedReference(
                original_text="it",
                resolved_text="the NIS2 Directive",
                reference_type="pronoun",
                resolution_source="preceding_chunk",
                position_in_chunk=42,
            )
        ],
        context_window_size=3,
        confidence=0.85,
    )


# === FIXTURES: Mock LLM ===


@pytest.fixture
def mock_llm_response() -> LLMResponse:
    """Standard mock LLM response."""
    return LLMResponse(
        content='{"summary": "Test summary"}',
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        latency_ms=500,
    )


@pytest.fixture
def mock_llm_client(mock_llm_response: LLMResponse) -> AsyncMock:
    """Mock BaseLLMClient with default response."""
    client = AsyncMock()
    client.complete = AsyncMock(return_value=mock_llm_response)
    client.complete_with_vision = AsyncMock(return_value=mock_llm_response)
    client.supports_vision = True
    client.provider_name = "mock"
    return client


# === FIXTURES: Temp dirs ===


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Temporary output directory."""
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """Temporary cache directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache
