# =============================================================================
# File: test_structural_extractor.py
# Version: 1
# Path: ay_platform_core/tests/unit/c7_memory/test_structural_extractor.py
# Description: Unit tests for the schema-guided L1 structural extractor
#              (V2 #3-A.a / R-400-200, R-400-201, E-400-006) :
#                - the closed ontology REJECTS out-of-ontology types
#                  (not coerced) and stamps EXTRACTED / conf 1.0 / L1 ;
#                - the deterministic requirements-corpus extractor maps
#                  R/D/T id blocks → REQUIREMENT/DECISION/TEST entities and
#                  `derives-from:` → DERIVES_FROM relations ;
#                - E-NNN ids (no ontology slot) are SKIPPED, not coerced ;
#                - extraction is deterministic + deduplicated.
#              No I/O — pure functions over in-memory text.
# =============================================================================

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ay_platform_core.c7_memory.kg.ontology import (
    ONTOLOGY_VERSION,
    StructuralEntity,
    StructuralRelation,
)
from ay_platform_core.c7_memory.kg.structural_extractor import extract_structural
from ay_platform_core.c7_memory.models import Provenance

pytestmark = pytest.mark.unit


def _block(entity_id: str, derives: str | None = None) -> str:
    lines = ["```yaml", f"id: {entity_id}", "version: 1", "status: draft"]
    if derives is not None:
        lines.append(f"derives-from: [{derives}]")
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Closed ontology (E-400-006)
# ---------------------------------------------------------------------------


class TestOntology:
    def test_valid_type_accepted_with_extracted_defaults(self) -> None:
        ent = StructuralEntity(name="R-400-200", type="REQUIREMENT")
        assert ent.provenance is Provenance.EXTRACTED
        assert ent.confidence == 1.0
        assert ent.layer == "L1"
        assert ent.ontology_version == ONTOLOGY_VERSION
        # Bi-temporal valid-time fields default to None (timeless ; D-019).
        assert ent.valid_from is None
        assert ent.valid_to is None

    def test_out_of_ontology_entity_type_rejected(self) -> None:
        # R-400-200 : an out-of-ontology type is REJECTED, not coerced.
        with pytest.raises(ValidationError):
            StructuralEntity(name="x", type="PERSON")  # type: ignore[arg-type]

    def test_out_of_ontology_relation_type_rejected(self) -> None:
        subj = StructuralEntity(name="R-1", type="REQUIREMENT")
        obj = StructuralEntity(name="D-1", type="DECISION")
        with pytest.raises(ValidationError):
            StructuralRelation(subject=subj, type="KILLS", object=obj)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Deterministic requirements-corpus extractor (R-400-200/201)
# ---------------------------------------------------------------------------


class TestStructuralExtractor:
    def test_extracts_entities_by_id_prefix(self) -> None:
        text = "\n\n".join(
            [_block("R-400-200"), _block("D-016"), _block("T-400-001")]
        )
        result = extract_structural(text)
        by_name = {e.name: e.type for e in result.entities}
        assert by_name == {
            "R-400-200": "REQUIREMENT",
            "D-016": "DECISION",
            "T-400-001": "TEST",
        }
        assert all(e.provenance is Provenance.EXTRACTED for e in result.entities)
        assert all(e.confidence == 1.0 for e in result.entities)

    def test_derives_from_becomes_relations(self) -> None:
        text = _block("R-400-200", derives="D-016, D-004")
        result = extract_structural(text)
        # D-004 is in-ontology (DECISION) ; both edges materialise.
        edges = {(r.subject.name, r.type, r.object.name) for r in result.relations}
        assert ("R-400-200", "DERIVES_FROM", "D-016") in edges
        assert ("R-400-200", "DERIVES_FROM", "D-004") in edges
        # The derives-from targets are pulled in as entities too.
        assert {"R-400-200", "D-016", "D-004"} <= {e.name for e in result.entities}

    def test_out_of_ontology_id_is_skipped_not_coerced(self) -> None:
        # E-400-006 has no entity-type slot for `E-` ids → skip them.
        text = _block("E-400-007") + "\n\n" + _block("R-1", derives="E-400-007")
        result = extract_structural(text)
        names = {e.name for e in result.entities}
        assert "E-400-007" not in names
        assert "R-1" in names
        # No edge to the skipped target.
        assert all(r.object.name != "E-400-007" for r in result.relations)

    def test_block_without_id_is_ignored(self) -> None:
        text = "```yaml\nversion: 1\nstatus: draft\n```"
        result = extract_structural(text)
        assert result.entities == []
        assert result.relations == []

    def test_no_fenced_blocks_yields_empty(self) -> None:
        result = extract_structural("# A heading\n\nSome prose mentioning R-400-200.")
        assert result.entities == []
        assert result.relations == []

    def test_extraction_is_deterministic_and_deduplicated(self) -> None:
        text = "\n\n".join([_block("R-1", derives="D-1"), _block("R-1", derives="D-1")])
        first = extract_structural(text)
        second = extract_structural(text)
        # Idempotent : repeated block collapses to one entity / one edge.
        assert len(first.entities) == 2  # R-1 + D-1
        assert len(first.relations) == 1
        assert [e.model_dump() for e in first.entities] == [
            e.model_dump() for e in second.entities
        ]
