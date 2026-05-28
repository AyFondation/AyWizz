# =============================================================================
# File: ontology.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/kg/ontology.py
# Description: Closed entity/relation ontology for the schema-guided L1
#              structural extractor (V2 #3-A.a). Operationalises E-400-006
#              (the `CodeKnowledgeOntology`) and R-400-200/201.
#
#              The structural extractor (R-400-200) operates over artifacts
#              whose ontology is KNOWN a priori — the project's own `code`
#              and `requirements` corpus — and is a DISTINCT component from
#              the existing open-domain document extractor (`kg/extractor.py`,
#              which legitimately stays open-domain per R-400-200's scope
#              note). Out-of-ontology types are REJECTED, never coerced.
#
#              Forward-compatibility (operator directive : keep A/B/C
#              coherent) : the structural records carry a `layer` field
#              (L0..L3, R-400-205) and `ontology_version` (R-400-207/208) so
#              the v2 layered graph + replay/versioning land without a
#              destructive model migration. Bi-temporal validity fields
#              (V2 #3-C) are intentionally NOT added here — they are a §8.1
#              spec gap (not in D-016) pending a ratified amendment.
# =============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c7_memory.models import Provenance

# Bump on any change to the vocabularies below (E-400-006 versioning). Stamped
# onto every structural record so a replay (R-400-207) reproduces the exact
# stored state and an ontology change is an explicit re-extraction decision.
ONTOLOGY_VERSION = 1

# Closed `code`-domain vocabularies (E-400-006). Expressed as `Literal` so
# `mypy --strict` AND Pydantic runtime validation both reject out-of-ontology
# types. The IMPLEMENTS / VALIDATES / DERIVES_FROM verbs deliberately match
# the `@relation` markers of meta/100-SPEC-METHODOLOGY §8 and the C6
# traceability checks — the L1 graph and the coherence engine share one
# vocabulary.
EntityType = Literal[
    "MODULE",
    "CLASS",
    "FUNCTION",
    "METHOD",
    "REQUIREMENT",
    "DECISION",
    "TEST",
    "CONTRACT",
]
RelationType = Literal[
    "IMPORTS",
    "CALLS",
    "DEFINES",
    "INHERITS_FROM",
    "IMPLEMENTS",
    "VALIDATES",
    "DERIVES_FROM",
    "REFERENCES",
]

# Vertical abstraction layer (D-016 / R-400-205). The structural extractor
# produces L1 ; L0 (verbatim) is the chunk store, L2/L3 (semantic/thematic)
# are v2 scope. Carried now so the v2 layered graph is additive.
KnowledgeLayer = Literal["L0", "L1", "L2", "L3"]


class StructuralEntity(BaseModel):
    """An L1 entity from the schema-guided extractor. `type` is constrained
    to the closed `EntityType` ontology (R-400-200) — an out-of-ontology
    value fails validation. Deterministic extraction → `EXTRACTED` /
    confidence 1.0 (R-400-201)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    type: EntityType
    provenance: Provenance = Provenance.EXTRACTED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    layer: KnowledgeLayer = "L1"
    ontology_version: int = ONTOLOGY_VERSION
    # Valid-time (D-019 / R-400-209) — when the fact holds in the modelled
    # domain. Both nullable : null = timeless (open interval). Transaction-time
    # (recorded_at / superseded_at) is a persistence concern set by the repo.
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class StructuralRelation(BaseModel):
    """A directed L1 relation between two structural entities, constrained to
    the closed `RelationType` ontology (R-400-200)."""

    model_config = ConfigDict(extra="forbid")

    subject: StructuralEntity
    type: RelationType
    object: StructuralEntity
    provenance: Provenance = Provenance.EXTRACTED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    layer: KnowledgeLayer = "L1"
    ontology_version: int = ONTOLOGY_VERSION
    # Valid-time (D-019 / R-400-209). Null = timeless.
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class StructuralExtraction(BaseModel):
    """Result envelope of one structural extraction pass over a source."""

    model_config = ConfigDict(extra="forbid")

    entities: list[StructuralEntity] = Field(default_factory=list)
    relations: list[StructuralRelation] = Field(default_factory=list)
    ontology_version: int = ONTOLOGY_VERSION


class StructuralKGResult(BaseModel):
    """Response of `POST /sources/{sid}/extract-structural` — counts of newly
    persisted records plus the extracted graph. Empty lists are a legitimate
    outcome (a source with no in-ontology entities)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    entities_added: int
    relations_added: int
    ontology_version: int = ONTOLOGY_VERSION
    entities: list[StructuralEntity] = Field(default_factory=list)
    relations: list[StructuralRelation] = Field(default_factory=list)


# Spec entity-id prefix → closed ontology entity type. Only the prefixes with
# a slot in E-400-006 are mapped (R/D/T) ; others (notably `E-` architecture
# entities) are absent so they are SKIPPED, never coerced (R-400-200).
_SPEC_ID_TYPE: dict[str, EntityType] = {
    "R": "REQUIREMENT",
    "D": "DECISION",
    "T": "TEST",
}


def entity_for_spec_id(spec_id: str) -> StructuralEntity | None:
    """Map a spec entity id (`R-`/`D-`/`T-NNN`) to an in-ontology
    `StructuralEntity`, or None when the id's prefix has no ontology slot
    (e.g. `E-NNN`) — skip, never coerce (R-400-200). Shared by the
    requirements-corpus and code (`@relation`-marker) extractors."""
    etype = _SPEC_ID_TYPE.get(spec_id[:1])
    if etype is None:
        return None
    return StructuralEntity(name=spec_id, type=etype)
