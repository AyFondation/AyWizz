<!-- =============================================================================
File: 2026-05-22-v2-3-aa-structural-extraction.md
Version: 1
Path: .claude/sessions/2026-05-22-v2-3-aa-structural-extraction.md
============================================================================= -->

# Session — 2026-05-22 — V2 #3-A.a : schema-guided L1 structural extractor

## Context

Third V2 feature ("Graphiti"). Scoping established that the spec operationalises
D-016 as an **ArangoDB-native** layered KG + iterative retrieval (999 §D-016,
400 §4.9), NOT the getzep `graphiti` library (Neo4j/FalkorDB — would break the
D-002 unified store). Operator chose "tout, dans l'ordre" → A (v1-compatible
subset) → B (v2 GraphRAG, D-010-gated) → C (bi-temporal, §8.1 gap). This session
delivered **A.a** : the schema-guided L1 structural extractor (R-400-200/201,
E-400-006), in two increments.

## Work delivered

**Closed ontology — `kg/ontology.py`.** `EntityType` / `RelationType` as
`Literal` (E-400-006 : MODULE/CLASS/FUNCTION/METHOD/REQUIREMENT/DECISION/TEST/
CONTRACT ; IMPORTS/CALLS/DEFINES/INHERITS_FROM/IMPLEMENTS/VALIDATES/
DERIVES_FROM/REFERENCES) → out-of-ontology types rejected by Pydantic + mypy.
`StructuralEntity`/`StructuralRelation` carry `provenance=EXTRACTED`,
`confidence=1.0`, `layer` (L0..L3, forward-compat for B), `ontology_version`.
Shared `entity_for_spec_id` (R/D/T → entity ; E-NNN → None, skip not coerce).

**Increment 1 — requirements-corpus extractor** (`kg/structural_extractor.py`,
deterministic, NO dependency). Parses `id:` / `derives-from:` declarations →
REQUIREMENT/DECISION/TEST + DERIVES_FROM. Made **whitespace-agnostic** (ordered
scan, not fenced-block regex) after finding the C7 chunker collapses newlines —
so it works on raw markdown AND chunk-reconstructed text.

**Increment 2 — persistence + endpoint.** `KGRepository.persist_structural`
writes the existing `memory_kg_entities`/`memory_kg_relations` collections plus
`layer`/`ontology_version`, with an EXACT insert count (idempotent re-run → 0
added — fixed the loose `source_ids==[sid]` heuristic copied from the open-domain
path). `MemoryService.extract_structural_kg` + `POST .../extract-structural`
(role gate = `extract-kg`), catalog row + `065-TEST-MATRIX.md` regenerated.

**Increment 3 — code-AST Python** (`kg/code_extractor.py`, **tree-sitter**).
`extract_structural_python` : MODULE/CLASS/FUNCTION/METHOD + DEFINES/IMPORTS/
INHERITS_FROM + `@relation` (IMPLEMENTS/VALIDATES/DERIVES_FROM) markers.
`extract-structural?kind=code` reads the ORIGINAL raw bytes via `get_source_blob`
+ `parse` (Python indentation is significant — chunk text won't do). NEW
component, distinct from the open-domain LLM doc extractor (R-400-200 scope
note). CALLS edges + other languages (TS/YAML/MD, in the language pack) deferred.

## Dependencies

Operator rebuilt the VM image with **latest-stable** deps (§5.2 explicitly
requested). Added to base : `tree-sitter` 0.25 + `tree-sitter-language-pack`
1.8 (`pyproject.toml` v11). Confirmed no other upcoming lib is needed (A.b/A.c
reuse ArangoSearch + C8 ; B is AQL-native ; C is a data-model change) — OpenHands
stays the C15-runner optional extra. Existing pins keep `>=x,<next-major`
ranges (resolve to latest-within-major on install ; no untested major bump).

## Findings / decisions

- **tree-sitter Rust binding** exposes node accessors as METHODS (`kind()`,
  `start_byte()`, `root_node()`) — mismatching the official property-based
  stubs. Resolved by typing the parser/nodes as `Any` (no override needed ;
  the language pack ships py.typed).
- **2 dep-refresh regressions fixed** (consequences of "latest stable") :
  (1) `nats-py` is now type-strict → the `pub._js` test-fake injection needs
  `# type: ignore[assignment]` ; (2) the K8s e2e fixture errored on the rebuilt
  VM's invalid `KUBECONFIG` — its reachability gate caught only `ApiException`,
  not `ConfigException` (§10.3 B) → now skips cleanly on an invalid kubeconfig.
- **B is D-010-gated** (re-flag before coding) ; **C (bi-temporal) is a §8.1
  spec gap** (D-016 amendment + validation before any code).

## Verification

`run_tests.sh ci` — ruff OK → mypy OK → pytest **1593 passed, 1 skipped**
(K8s e2e, no cluster), coverage **87.25%**. (CI surfaced + fixed mid-session :
the count heuristic, a coherence parallel-definition false-positive, an import
order, the 2 refresh regressions.)

## Next

V2 #3-A.b — hybrid retrieval BM25 (ArangoSearch) + dense + RRF (R-400-202).
