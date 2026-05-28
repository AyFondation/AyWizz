# =============================================================================
# File: test_kg_provenance.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_kg_provenance.py
# Description: Unit tests for R-400-201 — every KG node/edge carries a
#              `provenance` (EXTRACTED|INFERRED) and a `confidence` in
#              [0,1]. Verifies the model defaults, the bounds validation,
#              and that the LLM-based C7 source extractor yields INFERRED
#              records via the model default (no container needed).
#
# @relation validates:R-400-201
# =============================================================================

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ay_platform_core.c7_memory.kg.extractor import _expose_test_internals
from ay_platform_core.c7_memory.models import KGEntity, KGRelation, Provenance


def _entity(name: str = "Marie Curie", type_: str = "person") -> KGEntity:
    return KGEntity(name=name, type=type_)


# ---------------------------------------------------------------------------
# Model defaults & validation
# ---------------------------------------------------------------------------


def test_entity_defaults_to_inferred_with_full_confidence() -> None:
    """A KG node built without explicit provenance is INFERRED at 1.0.

    INFERRED is the safe default: the only current producer is the LLM
    source extractor. A deterministic extractor must opt into EXTRACTED.
    """
    entity = _entity()
    assert entity.provenance is Provenance.INFERRED
    assert entity.confidence == 1.0


def test_relation_defaults_to_inferred_with_full_confidence() -> None:
    relation = KGRelation(
        subject=_entity(),
        relation="discovered",
        object=_entity("Polonium", "concept"),
    )
    assert relation.provenance is Provenance.INFERRED
    assert relation.confidence == 1.0


def test_extracted_provenance_is_settable() -> None:
    """A deterministic extractor sets EXTRACTED with confidence 1.0."""
    entity = KGEntity(
        name="parse_pdf",
        type="function",
        provenance=Provenance.EXTRACTED,
        confidence=1.0,
    )
    assert entity.provenance is Provenance.EXTRACTED
    assert entity.confidence == 1.0


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01, 2.0, -1.0])
def test_confidence_out_of_unit_interval_is_rejected(bad_confidence: float) -> None:
    """Confidence is a probability-like score: it MUST stay in [0,1]."""
    with pytest.raises(ValidationError):
        KGEntity(name="x", type="concept", confidence=bad_confidence)
    with pytest.raises(ValidationError):
        KGRelation(
            subject=_entity(),
            relation="rel",
            object=_entity("o", "concept"),
            confidence=bad_confidence,
        )


def test_confidence_bounds_inclusive() -> None:
    """0.0 and 1.0 are both valid (inclusive bounds)."""
    assert KGEntity(name="x", type="c", confidence=0.0).confidence == 0.0
    assert KGEntity(name="x", type="c", confidence=1.0).confidence == 1.0


def test_provenance_enum_is_a_string_enum() -> None:
    """StrEnum so it serialises to a plain string for Arango / JSON."""
    assert Provenance.EXTRACTED.value == "extracted"
    assert Provenance.INFERRED.value == "inferred"
    assert KGEntity(name="x", type="c").model_dump()["provenance"] == "inferred"


# ---------------------------------------------------------------------------
# Extractor parse path tags LLM output as INFERRED
# ---------------------------------------------------------------------------


def test_extractor_parse_tags_llm_output_as_inferred() -> None:
    """The LLM returns only {name,type}/{subject,relation,object}; the
    extractor's parse path SHALL still produce records tagged INFERRED
    (via the model default), so every persisted node/edge is honest about
    being LLM-derived (R-400-201)."""
    parse_response = _expose_test_internals()["parse_response"]
    payload = json.dumps(
        {
            "entities": [
                {"name": "Marie Curie", "type": "person"},
                {"name": "Polonium", "type": "concept"},
            ],
            "relations": [
                {
                    "subject": {"name": "Marie Curie", "type": "person"},
                    "relation": "discovered",
                    "object": {"name": "Polonium", "type": "concept"},
                }
            ],
        }
    )

    entities, relations = parse_response(payload)

    assert len(entities) == 2
    assert len(relations) == 1
    assert all(e.provenance is Provenance.INFERRED for e in entities)
    assert all(0.0 <= e.confidence <= 1.0 for e in entities)
    assert relations[0].provenance is Provenance.INFERRED
    # The nested subject/object entities are also INFERRED, not just the edge.
    assert relations[0].subject.provenance is Provenance.INFERRED
    assert relations[0].object.provenance is Provenance.INFERRED
