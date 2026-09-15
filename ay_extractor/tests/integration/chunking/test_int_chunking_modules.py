# tests/integration/chunking/test_int_chunking_modules.py — v1
"""Integration tests for text chunking pipeline.

Covers: structural_chunker, semantic_chunker, chunk_validator, chunker_factory.
Pure Python — no external services.
"""

from __future__ import annotations

import pytest
from ayextractor.config.settings import Settings
from ayextractor.core.models import Chunk
from ayextractor.extraction.structure_detector import detect_structure

# ── Helpers ─────────────────────────────────────────────────────

LONG_TEXT = """# Introduction

The European Union has enacted several cybersecurity regulations in recent years.
These include the NIS2 Directive and the Cyber Resilience Act (CRA).

## NIS2 Directive

The NIS2 Directive (EU 2022/2555) is the primary legislation for network and information
security across the European Union. It was adopted in December 2022 and member states
have until October 2024 to transpose it into national law. The directive significantly
expands the scope of cybersecurity requirements compared to the original NIS Directive.
It covers essential entities such as energy providers, transport operators, banking
institutions, and digital infrastructure providers.

## Cyber Resilience Act

The CRA establishes horizontal cybersecurity requirements for products with digital
elements. It covers hardware and software products placed on the EU market.
Manufacturers must ensure products are designed with security in mind throughout
their lifecycle. The act introduces conformity assessment procedures and requires
vulnerability handling processes. Products must be accompanied by documentation
including a software bill of materials (SBOM).

## ISO 21434

ISO/SAE 21434 is the international standard for road vehicle cybersecurity engineering.
It specifies cybersecurity requirements for the full vehicle lifecycle, from concept
to decommissioning. The standard is closely aligned with UNECE WP.29 Regulation 155
which mandates Cybersecurity Management Systems (CSMS) for vehicle type approval.

## Conclusion

Organizations operating in the EU must carefully assess their compliance obligations
under these various regulatory frameworks. Early preparation is key to successful
compliance implementation."""

# Use the REAL detector output (true offsets + levels) rather than hand-typed
# positions — the structure-aware chunker slices the text on these spans, so
# fake offsets would mis-cut the chunks.
STRUCTURE = detect_structure(LONG_TEXT)


# ── Structural Chunker ─────────────────────────────────────────

class TestStructuralChunker:

    @pytest.mark.asyncio
    async def test_chunk_produces_results(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        chunker = StructuralChunker(Settings(_env_file=None, chunk_target_size=500))
        chunks = await chunker.chunk(LONG_TEXT, structure=STRUCTURE, source_file="test.md")
        assert len(chunks) >= 2

    @pytest.mark.asyncio
    async def test_chunk_ids_unique(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        chunker = StructuralChunker(Settings(_env_file=None, chunk_target_size=500))
        chunks = await chunker.chunk(LONG_TEXT, structure=STRUCTURE)
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_chunk_content_covers_input(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        chunker = StructuralChunker(Settings(_env_file=None, chunk_target_size=500))
        chunks = await chunker.chunk(LONG_TEXT)
        combined = " ".join(c.content for c in chunks)
        for keyword in ["NIS2", "CRA", "ISO 21434", "cybersecurity"]:
            assert keyword in combined

    @pytest.mark.asyncio
    async def test_empty_text(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        chunker = StructuralChunker()
        chunks = await chunker.chunk("")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_atomic_blocks_preserved(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        text = (
            'Before. <<<IMAGE_CONTENT id="img1">>>'
            "Image description<<<END_IMAGE_CONTENT>>> After."
        )
        chunker = StructuralChunker(Settings(_env_file=None, chunk_target_size=100))
        chunks = await chunker.chunk(text)
        # Atomic block should not be split
        found = any("IMAGE_CONTENT" in c.content for c in chunks)
        assert found

    @pytest.mark.asyncio
    async def test_strategy_name(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        assert StructuralChunker().strategy_name == "structural"

    @pytest.mark.asyncio
    async def test_large_target_produces_fewer_chunks(self):
        from ayextractor.chunking.structural_chunker import StructuralChunker
        small = StructuralChunker(Settings(_env_file=None, chunk_target_size=300))
        large = StructuralChunker(Settings(_env_file=None, chunk_target_size=2000))
        small_chunks = await small.chunk(LONG_TEXT)
        large_chunks = await large.chunk(LONG_TEXT)
        assert len(small_chunks) >= len(large_chunks)


# ── Semantic Chunker ────────────────────────────────────────────

class TestSemanticChunker:

    @pytest.mark.asyncio
    async def test_chunk_produces_results(self):
        from ayextractor.chunking.semantic_chunker import SemanticChunker
        chunker = SemanticChunker(Settings(_env_file=None, chunk_target_size=500))
        chunks = await chunker.chunk(LONG_TEXT)
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_strategy_name(self):
        from ayextractor.chunking.semantic_chunker import SemanticChunker
        assert SemanticChunker().strategy_name == "semantic"

    @pytest.mark.asyncio
    async def test_empty_text(self):
        from ayextractor.chunking.semantic_chunker import SemanticChunker
        chunks = await SemanticChunker().chunk("")
        assert chunks == []


# ── Chunk Validator ─────────────────────────────────────────────

class TestChunkValidator:

    @pytest.mark.asyncio
    async def test_valid_chunks_pass(self):
        from ayextractor.chunking.chunk_validator import validate_chunks
        from ayextractor.chunking.structural_chunker import StructuralChunker
        chunker = StructuralChunker(Settings(_env_file=None, chunk_target_size=500))
        chunks = await chunker.chunk(LONG_TEXT)
        result = validate_chunks(chunks)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_empty_chunks_valid(self):
        from ayextractor.chunking.chunk_validator import validate_chunks
        result = validate_chunks([])
        assert result.valid is True

    def test_oversized_chunk_reported(self):
        from ayextractor.chunking.chunk_validator import validate_chunks
        big_chunk = Chunk(
            id="big", position=0, content="x" * 20000,
            source_file="test.pdf", source_pages=[1],
            source_sections=[], char_count=20000, word_count=20000,
            token_count_est=15000, fingerprint="abc",
            primary_language="en", key_entities=[],
        )
        result = validate_chunks([big_chunk], max_chunk_size=10000)
        assert len(result.warnings) > 0 or len(result.errors) > 0

    def test_broken_atomic_block_detected(self):
        from ayextractor.chunking.chunk_validator import validate_chunks
        broken = Chunk(
            id="broken", position=0,
            content='<<<IMAGE_CONTENT id="img1">>>start of image',
            source_file="test.pdf", source_pages=[1],
            source_sections=[], char_count=100, word_count=10,
            token_count_est=15, fingerprint="def",
            primary_language="en", key_entities=[],
        )
        result = validate_chunks([broken])
        assert len(result.errors) > 0 or len(result.warnings) > 0


# ── Chunker Factory ─────────────────────────────────────────────

class TestChunkerFactory:

    def test_create_structural(self):
        from ayextractor.chunking.chunker_factory import create_chunker
        chunker = create_chunker(Settings(_env_file=None, chunking_strategy="structural"))
        assert chunker.strategy_name == "structural"

    def test_create_semantic(self):
        from ayextractor.chunking.chunker_factory import create_chunker
        chunker = create_chunker(Settings(_env_file=None, chunking_strategy="semantic"))
        assert chunker.strategy_name == "semantic"

    def test_default(self):
        from ayextractor.chunking.chunker_factory import create_chunker
        chunker = create_chunker()
        assert chunker.strategy_name in ("structural", "semantic")
