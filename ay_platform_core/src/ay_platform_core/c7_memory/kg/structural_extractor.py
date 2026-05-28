# =============================================================================
# File: structural_extractor.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/kg/structural_extractor.py
# Description: Deterministic schema-guided L1 structural extractor for the
#              requirements corpus (V2 #3-A.a / R-400-200, R-400-201,
#              E-400-006).
#
#              This is the NEW known-ontology structural extractor R-400-200
#              mandates — DISTINCT from the open-domain LLM document extractor
#              in `extractor.py`. It is purely deterministic (no LLM, no
#              network) : it parses requirement-spec entity blocks
#              (```yaml ... id: <R|D|T>-NNN ... derives-from: [...] ```) into
#              the closed ontology — REQUIREMENT / DECISION / TEST entities and
#              DERIVES_FROM relations — so every record is `EXTRACTED` with
#              confidence 1.0 (R-400-201).
#
#              Out-of-ontology ids (e.g. `E-NNN`, which has no slot in
#              E-400-006's entity vocabulary) are SKIPPED, never coerced into
#              a free-form type (R-400-200). Code-domain symbol extraction
#              (MODULE/CLASS/FUNCTION/METHOD via tree-sitter, D-004) and
#              `@relation`-marker edges (IMPLEMENTS/VALIDATES) are a follow-on
#              increment of A.a (tree-sitter is a §5.2 dependency decision).
# =============================================================================

from __future__ import annotations

import re

from ay_platform_core.c7_memory.kg.ontology import (
    StructuralEntity,
    StructuralExtraction,
    StructuralRelation,
    entity_for_spec_id,
)

# An entity declaration (`id: R-400-200`) OR a `derives-from: [D-016, D-004]`
# list, matched as a single alternation so we can walk them IN ORDER and
# attach each `derives-from:` to the entity whose `id:` precedes it. This is
# deliberately whitespace-AGNOSTIC (no `^`/fence anchors) so it works equally
# on raw markdown AND on chunk-reconstructed text, where the C7 chunker has
# collapsed every run of whitespace (incl. newlines) to single spaces.
_DECL_RE = re.compile(
    r"(?:\bid:\s*(?P<id>(?:R|E|D|T)-[A-Z0-9-]+))"
    r"|(?:\bderives-from:\s*\[(?P<targets>[^\]]*)\])"
)
# A single entity-id token (used to split a `derives-from:` list).
_ID_TOKEN_RE = re.compile(r"(?:R|E|D|T)-[A-Z0-9-]+")


def extract_structural(text: str) -> StructuralExtraction:
    """Extract the L1 structural graph from a requirements-spec document.

    Deterministic and idempotent : the same text always yields the same
    entities/relations (deduplicated by id / triple). Entities come from
    `id:` declarations ; DERIVES_FROM edges from each `derives-from:` list,
    attached to the entity whose `id:` most recently preceded it. All records
    are `EXTRACTED` / confidence 1.0 (R-400-201). An out-of-ontology id (e.g.
    `E-NNN`) yields no entity, so a `derives-from:` that would attach to it
    is dropped — never coerced."""
    entities: dict[str, StructuralEntity] = {}
    relations: dict[tuple[str, str], StructuralRelation] = {}
    current: StructuralEntity | None = None

    for match in _DECL_RE.finditer(text):
        declared_id = match.group("id")
        if declared_id is not None:
            current = entity_for_spec_id(declared_id)
            if current is not None:
                entities[current.name] = current
            continue
        if current is None:
            continue  # derives-from with no in-ontology owner — skip
        for target_id in _ID_TOKEN_RE.findall(match.group("targets")):
            obj = entity_for_spec_id(target_id)
            if obj is None:
                continue
            entities.setdefault(obj.name, obj)
            key = (current.name, obj.name)
            relations[key] = StructuralRelation(
                subject=current, type="DERIVES_FROM", object=obj
            )

    return StructuralExtraction(
        entities=list(entities.values()), relations=list(relations.values())
    )
