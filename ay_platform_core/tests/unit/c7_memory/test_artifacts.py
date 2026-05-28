# =============================================================================
# File: test_artifacts.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_artifacts.py
# Description: Unit tests for R-400-207 processing-artifact (de)serialisation.
#              Pure round-trips, no I/O: verifies chunks.json and kg.json
#              survive serialise -> deserialise byte-for-byte (semantically),
#              that KG provenance/confidence (R-400-201) is preserved on
#              replay, and that a corrupted artifact fails loudly on load.
#
# @relation validates:R-400-207
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c7_memory.artifacts import (
    CHUNKS_ARTIFACT,
    KG_ARTIFACT,
    deserialize_chunks,
    deserialize_kg,
    serialize_chunks,
    serialize_kg,
)
from ay_platform_core.c7_memory.models import KGEntity, KGRelation, Provenance


def test_artifact_names_are_stable() -> None:
    assert CHUNKS_ARTIFACT == "chunks.json"
    assert KG_ARTIFACT == "kg.json"


def test_chunks_round_trip_preserves_rows_and_model() -> None:
    rows = [
        {"chunk_id": "s1:0", "content": "alpha", "vector": [0.1, 0.2], "chunk_index": 0},
        {"chunk_id": "s1:1", "content": "beta", "vector": [0.3, 0.4], "chunk_index": 1},
    ]
    artifact = deserialize_chunks(serialize_chunks("s1", "model-x", rows))
    assert artifact.source_id == "s1"
    assert artifact.model_id == "model-x"
    assert artifact.chunks == rows  # rows survive byte-for-byte (incl. vectors)


def test_kg_round_trip_preserves_entities_relations_and_provenance() -> None:
    """A replay MUST reproduce the exact triples AND their provenance, so
    the rebuilt graph is honest about being LLM-inferred (R-400-201)
    without re-invoking the model (R-400-207)."""
    marie = KGEntity(name="Marie Curie", type="person")  # default INFERRED
    polonium = KGEntity(name="Polonium", type="concept")
    relation = KGRelation(subject=marie, relation="discovered", object=polonium)

    artifact = deserialize_kg(
        serialize_kg("s1", [marie, polonium], [relation], extraction_model_id="claude-x")
    )

    assert artifact.source_id == "s1"
    assert artifact.extraction_model_id == "claude-x"
    assert [e.name for e in artifact.entities] == ["Marie Curie", "Polonium"]
    assert all(e.provenance is Provenance.INFERRED for e in artifact.entities)
    assert artifact.relations[0].relation == "discovered"
    assert artifact.relations[0].provenance is Provenance.INFERRED
    assert artifact.relations[0].subject.name == "Marie Curie"


def test_kg_round_trip_preserves_extracted_provenance() -> None:
    """A deterministic-extractor artifact keeps EXTRACTED/1.0 on replay."""
    node = KGEntity(
        name="parse_pdf",
        type="function",
        provenance=Provenance.EXTRACTED,
        confidence=1.0,
    )
    artifact = deserialize_kg(serialize_kg("s2", [node], []))
    assert artifact.entities[0].provenance is Provenance.EXTRACTED
    assert artifact.entities[0].confidence == 1.0


def test_kg_extraction_model_id_optional() -> None:
    artifact = deserialize_kg(serialize_kg("s3", [], []))
    assert artifact.extraction_model_id is None
    assert artifact.entities == []
    assert artifact.relations == []


def test_corrupt_kg_artifact_fails_loudly() -> None:
    """A confidence outside [0,1] in a stored artifact is rejected on load
    (the model validation re-runs), not silently ingested."""
    bad = b'{"source_id":"s","entities":[{"name":"x","type":"c","confidence":9.0}],"relations":[]}'
    with pytest.raises(ValidationError):
        deserialize_kg(bad)


def test_corrupt_chunks_artifact_fails_loudly() -> None:
    with pytest.raises(ValidationError):
        deserialize_chunks(b'{"not":"a-chunks-artifact"}')
