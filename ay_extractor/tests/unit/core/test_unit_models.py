# tests/unit/core/test_models.py — v1
"""Tests for core/models.py — all shared Pydantic models.

Also covers version.py import validation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ayextractor.core.models import (
    Chunk,
    ChunkDecontextualization,
    ChunkSourceSection,
    Concept,
    ConsolidatedTriplet,
    DocumentStructure,
    EntityNormalization,
    ExtractionResult,
    Footnote,
    ImageAnalysis,
    QualifiedTriplet,
    Reference,
    Relation,
    RelationTaxonomyEntry,
    ResolvedReference,
    Section,
    SourceMetadata,
    SourceProvenance,
    TableData,
    TemporalScope,
    Theme,
    TokenBudget,
)
from ayextractor.version import __spec_version__, __version__


# === VERSION ===


class TestVersion:
    def test_version_format(self):
        assert __version__
        parts = __version__.split(".")
        assert len(parts) == 3

    def test_spec_version(self):
        assert __spec_version__ == "2.1.8"


# === TEMPORAL & PROVENANCE ===


class TestSourceProvenance:
    def test_create(self, sample_provenance):
        assert sample_provenance.document_id == "20260207_140000_a1b2c3d4"
        assert sample_provenance.extraction_confidence == 0.9

    def test_serialization_roundtrip(self, sample_provenance):
        data = sample_provenance.model_dump()
        restored = SourceProvenance(**data)
        assert restored == sample_provenance


class TestTemporalScope:
    def test_point(self):
        ts = TemporalScope(type="point", start="2025-03-15", raw_expression="March 15, 2025")
        assert ts.type == "point"
        assert ts.end is None

    def test_range(self, sample_temporal_scope):
        assert sample_temporal_scope.type == "range"
        assert sample_temporal_scope.granularity == "month"

    def test_recurring(self):
        ts = TemporalScope(type="recurring", raw_expression="every quarter")
        assert ts.type == "recurring"


# === CHUNK MODELS ===


class TestChunk:
    def test_minimal_chunk(self, sample_chunk):
        assert sample_chunk.id == "chunk_001"
        assert sample_chunk.position == 0
        assert len(sample_chunk.content) > 0

    def test_defaults(self):
        c = Chunk(
            id="t",
            position=0,
            content="text",
            source_file="f.pdf",
            fingerprint="x",
        )
        assert c.content_type == "text"
        assert c.embedded_images == []
        assert c.embedding is None
        assert c.is_multilingual is False

    def test_linked_chunks(self, sample_chunk_list):
        assert sample_chunk_list[0].following_chunk_id == "chunk_002"
        assert sample_chunk_list[1].preceding_chunk_id == "chunk_001"
        assert sample_chunk_list[2].preceding_chunk_id == "chunk_002"

    def test_with_decontextualization(self, sample_chunk, sample_decontextualization):
        c = sample_chunk.model_copy(update={"decontextualization": sample_decontextualization})
        assert c.decontextualization is not None
        assert c.decontextualization.applied is True
        assert len(c.decontextualization.resolved_references) == 1

    def test_serialization_roundtrip(self, sample_chunk):
        data = sample_chunk.model_dump()
        restored = Chunk(**data)
        assert restored.id == sample_chunk.id
        assert restored.content == sample_chunk.content


class TestChunkSourceSection:
    def test_create(self):
        s = ChunkSourceSection(title="3.2 Risk", level=2)
        assert s.title == "3.2 Risk"
        assert s.level == 2


class TestResolvedReference:
    def test_create(self):
        r = ResolvedReference(
            original_text="il",
            resolved_text="Marc Dupont",
            reference_type="pronoun",
            resolution_source="preceding_chunk",
            position_in_chunk=10,
        )
        assert r.reference_type == "pronoun"


# === TRIPLET MODELS ===


class TestQualifiedTriplet:
    def test_create(self, sample_triplet):
        assert sample_triplet.subject == "European Union"
        assert sample_triplet.confidence == 0.9

    def test_with_qualifiers(self):
        t = QualifiedTriplet(
            subject="A",
            predicate="uses",
            object="B",
            source_chunk_id="c1",
            confidence=0.8,
            context_sentence="A uses B",
            qualifiers={"scope": "EU", "instrument": "directive"},
        )
        assert t.qualifiers["scope"] == "EU"

    def test_with_temporal_scope(self, sample_temporal_scope):
        t = QualifiedTriplet(
            subject="A",
            predicate="regulates",
            object="B",
            source_chunk_id="c1",
            confidence=0.7,
            context_sentence="A regulates B",
            temporal_scope=sample_temporal_scope,
        )
        assert t.temporal_scope is not None
        assert t.temporal_scope.type == "range"


class TestConsolidatedTriplet:
    def test_create(self, sample_consolidated_triplet):
        assert sample_consolidated_triplet.occurrence_count == 2
        assert len(sample_consolidated_triplet.source_chunk_ids) == 2

    def test_merged_qualifiers(self):
        t = ConsolidatedTriplet(
            subject="A",
            predicate="regulates",
            object="B",
            source_chunk_ids=["c1"],
            occurrence_count=1,
            confidence=0.9,
            qualifiers={"scope": ["EU", "US"]},
        )
        assert isinstance(t.qualifiers["scope"], list)


class TestEntityNormalization:
    def test_create(self, sample_entity_normalization):
        assert sample_entity_normalization.canonical_name == "European Union"
        assert "EU" in sample_entity_normalization.aliases

    def test_entity_types(self):
        for etype in ["person", "organization", "concept", "location", "document", "technology"]:
            e = EntityNormalization(
                canonical_name="test",
                entity_type=etype,
                occurrence_count=1,
            )
            assert e.entity_type == etype


class TestRelationTaxonomyEntry:
    def test_create(self):
        r = RelationTaxonomyEntry(
            canonical_relation="regulates",
            original_forms=["regulates", "réglemente", "governs"],
            category="governance",
            is_directional=True,
        )
        assert r.category == "governance"
        assert r.is_directional is True


# === DOCUMENT STRUCTURE ===


class TestDocumentStructure:
    def test_defaults(self):
        ds = DocumentStructure()
        assert ds.has_toc is False
        assert ds.sections == []

    def test_with_sections(self):
        ds = DocumentStructure(
            has_toc=True,
            sections=[Section(title="Intro", level=1, start_position=0, end_position=100)],
        )
        assert len(ds.sections) == 1


class TestReference:
    def test_create(self):
        r = Reference(type="citation", text="[1] ISO 21434", source_chunk_id="c1")
        assert r.target is None


# === EXTRACTION MODELS ===


class TestExtractionResult:
    def test_create(self, sample_extraction_result):
        assert sample_extraction_result.language == "en"
        assert sample_extraction_result.structure.has_toc is True

    def test_with_images_and_tables(self):
        er = ExtractionResult(
            raw_text="text",
            enriched_text="text",
            images=[
                ImageAnalysis(
                    id="img_001",
                    type="diagram",
                    description="System architecture",
                    source_page=3,
                )
            ],
            tables=[
                TableData(
                    id="tbl_001",
                    content_markdown="| A | B |",
                    source_page=5,
                    origin="structured",
                )
            ],
        )
        assert len(er.images) == 1
        assert er.tables[0].origin == "structured"


# === API VIEW MODELS ===


class TestAPIViewModels:
    def test_theme(self):
        t = Theme(name="Cybersecurity", description="Regulatory framework", relevance_score=0.95)
        assert t.relevance_score == 0.95

    def test_concept(self):
        c = Concept(
            name="NIS2 Directive",
            description="organization: EU cybersecurity directive",
            aliases=["NIS2", "NIS 2"],
        )
        assert len(c.aliases) == 2

    def test_relation(self):
        r = Relation(source="EU", relation_type="regulates", target="cybersecurity", weight=0.9)
        assert r.weight == 0.9


# === UTILITY MODELS ===


class TestTokenBudget:
    def test_create(self):
        tb = TokenBudget(
            total_estimated=100000,
            per_agent={"summarizer": 20000, "densifier": 30000},
            consumed={"summarizer": 15000},
        )
        assert tb.total_estimated == 100000


class TestSourceMetadata:
    def test_create(self):
        sm = SourceMetadata(
            original_filename="report.pdf",
            format="pdf",
            size_bytes=2_450_000,
            sha256="aabbccdd" * 8,
            stored_at=datetime(2026, 2, 7, tzinfo=timezone.utc),
        )
        assert sm.format == "pdf"
