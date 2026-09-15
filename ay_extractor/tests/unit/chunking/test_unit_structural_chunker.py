# tests/unit/chunking/test_unit_structural_chunker.py — v1
"""Unit tests for the structure-aware StructuralChunker.

Drives the REAL `detect_structure` (no hand-typed offsets) so the chunker
slices on true section spans. Covers: reliable section_path from the heading
hierarchy, hard chapter boundaries (a chunk never straddles into another
chapter), option-B sibling merging (small same-parent subsections coalesce),
oversized-section sub-chunking, and the no-structure fallback.
"""

from __future__ import annotations

import pytest
from ayextractor.chunking.structural_chunker import StructuralChunker
from ayextractor.config.settings import Settings
from ayextractor.extraction.structure_detector import detect_structure

# Numbered headings → clean titles ("1 Introduction", not "# …").
DOC = """1 Introduction

This is the introduction body with several words to fill it out nicely.

1.1 Background

Background subsection, fairly short body text here.

1.2 Scope

Scope subsection, also a short body text right here.

2 Methods

Methods chapter body content goes here with some length to it.
"""


def _settings(target: int) -> Settings:
    return Settings(_env_file=None, chunk_target_size=target)


def _paths(chunks) -> list[list[str]]:
    return [[s.title for s in c.source_sections] for c in chunks]


@pytest.mark.asyncio
async def test_detect_structure_gives_real_spans_and_levels():
    sections = detect_structure(DOC).sections
    titles = [s.title for s in sections]
    assert titles == ["1 Introduction", "1.1 Background", "1.2 Scope", "2 Methods"]
    assert [s.level for s in sections] == [1, 2, 2, 1]
    # end_position of each section is the next heading's start (real body span).
    for a, b in zip(sections, sections[1:], strict=False):
        assert a.end_position == b.start_position
    # The first section's body slice contains its own heading + body text.
    body0 = DOC[sections[0].start_position : sections[0].end_position]
    assert "introduction body" in body0


@pytest.mark.asyncio
async def test_section_path_is_hierarchical_not_substring():
    chunker = StructuralChunker(_settings(60))  # small → no sibling merge
    chunks = await chunker.chunk(DOC, structure=detect_structure(DOC))
    paths = _paths(chunks)
    # A subsection chunk carries its FULL ancestor chain (depth 2), reliably —
    # not the old substring guess that often produced [].
    assert ["1 Introduction", "1.1 Background"] in paths
    assert ["1 Introduction", "1.2 Scope"] in paths


@pytest.mark.asyncio
async def test_sibling_subsections_merge_option_b():
    chunker = StructuralChunker(_settings(2000))  # large → siblings coalesce
    chunks = await chunker.chunk(DOC, structure=detect_structure(DOC))
    merged = [c for c in chunks if "Background" in c.content and "Scope" in c.content]
    assert len(merged) == 1, "1.1 + 1.2 (same parent) should merge into one chunk"
    # The merged chunk is labelled with the common parent (LCP), not a leaf.
    assert [s.title for s in merged[0].source_sections] == ["1 Introduction"]


@pytest.mark.asyncio
async def test_never_crosses_a_chapter_boundary():
    chunker = StructuralChunker(_settings(2000))
    chunks = await chunker.chunk(DOC, structure=detect_structure(DOC))
    # No chunk mixes chapter-1 content with chapter-2 content.
    for c in chunks:
        mixes = ("Introduction" in c.content or "Background" in c.content) and (
            "Methods chapter" in c.content
        )
        assert not mixes


@pytest.mark.asyncio
async def test_oversized_section_is_subchunked_keeping_its_path():
    big_para_a = "Alpha " * 60
    big_para_b = "Beta " * 60
    doc = f"1 Big Section\n\n{big_para_a.strip()}\n\n{big_para_b.strip()}\n"
    chunker = StructuralChunker(_settings(200))
    chunks = await chunker.chunk(doc, structure=detect_structure(doc))
    assert len(chunks) >= 2, "an oversized section must split into multiple chunks"
    # Every resulting chunk still carries the section's path.
    for c in chunks:
        assert [s.title for s in c.source_sections] == ["1 Big Section"]


@pytest.mark.asyncio
async def test_no_structure_falls_back_to_paragraph_packing():
    plain = "First paragraph here.\n\nSecond paragraph follows.\n\nThird one too."
    chunker = StructuralChunker(_settings(40))
    chunks = await chunker.chunk(plain)  # no structure
    assert len(chunks) >= 1
    # Fallback chunks carry no section path (nothing to attribute).
    assert all(c.source_sections == [] for c in chunks)
    combined = " ".join(c.content for c in chunks)
    assert "First paragraph" in combined and "Third one" in combined
