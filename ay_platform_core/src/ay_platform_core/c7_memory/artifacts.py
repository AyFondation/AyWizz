# =============================================================================
# File: artifacts.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/artifacts.py
# Description: Pure (de)serialisation of the C7 processing artifacts that
#              make the vector + graph stores reconstructible by replay
#              (R-400-207). No I/O here — the service writes/reads the
#              bytes through MemorySourceStorage; this module only turns
#              the in-memory objects into stable JSON and back, validating
#              on load so a corrupted artifact fails loudly.
#
#              Two artifacts per source, under
#              `sources/{tenant}/{project}/{source_id}/`:
#                - chunks.json : the embedded chunk rows (text + vector +
#                                provenance metadata) -> replays the
#                                vector store with NO re-embedding.
#                - kg.json     : the extracted entities + relations (with
#                                provenance per R-400-201) -> replays the
#                                graph store with NO LLM call.
#
# @relation implements:R-400-207
# =============================================================================

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ay_platform_core.c7_memory.models import KGEntity, KGRelation

CHUNKS_ARTIFACT = "chunks.json"
KG_ARTIFACT = "kg.json"

# Bump when the on-disk artifact shape changes incompatibly.
_ARTIFACT_SCHEMA_VERSION = 1


class ChunksArtifact(BaseModel):
    """Replayable snapshot of a source's embedded chunk rows.

    `chunks` holds the exact `memory_chunks` row dicts produced at
    ingestion (text + vector + model_id + provenance metadata), so a
    rebuild is `upsert_chunks(chunks)` — the embedder is never called.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=_ARTIFACT_SCHEMA_VERSION)
    source_id: str
    model_id: str
    """Embedding model that produced the vectors. A rebuild reproduces the
    exact stored vectors; a model upgrade is an explicit re-embed, not an
    implicit side effect of replay (R-400-207)."""
    chunks: list[dict[str, Any]] = Field(default_factory=list)


class KGArtifact(BaseModel):
    """Replayable snapshot of a source's extracted knowledge graph.

    Entities/relations carry their `provenance` + `confidence` (R-400-201),
    so the replayed graph is byte-identical to the original LLM extraction
    — and honest about being INFERRED — without re-invoking the model.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=_ARTIFACT_SCHEMA_VERSION)
    source_id: str
    extraction_model_id: str | None = None
    """Model that produced the extraction, when known. Optional in v1: the
    reproducibility guarantee depends on the persisted triples, not on this
    field; it is recorded for upgrade tracking once the extractor surfaces
    the resolved provider model id."""
    entities: list[KGEntity] = Field(default_factory=list)
    relations: list[KGRelation] = Field(default_factory=list)


def serialize_chunks(source_id: str, model_id: str, chunk_rows: list[dict[str, Any]]) -> bytes:
    """Serialise embedded chunk rows into the chunks.json artifact."""
    artifact = ChunksArtifact(source_id=source_id, model_id=model_id, chunks=chunk_rows)
    return artifact.model_dump_json().encode("utf-8")


def deserialize_chunks(raw: bytes) -> ChunksArtifact:
    """Parse + validate a chunks.json artifact (raises on corruption)."""
    return ChunksArtifact.model_validate_json(raw)


def serialize_kg(
    source_id: str,
    entities: list[KGEntity],
    relations: list[KGRelation],
    *,
    extraction_model_id: str | None = None,
) -> bytes:
    """Serialise extracted entities + relations into the kg.json artifact."""
    artifact = KGArtifact(
        source_id=source_id,
        extraction_model_id=extraction_model_id,
        entities=entities,
        relations=relations,
    )
    return artifact.model_dump_json().encode("utf-8")


def deserialize_kg(raw: bytes) -> KGArtifact:
    """Parse + validate a kg.json artifact (raises on corruption). The
    nested KGEntity/KGRelation validation re-enforces R-400-201's
    provenance + confidence bounds on load."""
    return KGArtifact.model_validate_json(raw)


__all__ = [
    "CHUNKS_ARTIFACT",
    "KG_ARTIFACT",
    "ChunksArtifact",
    "KGArtifact",
    "deserialize_chunks",
    "deserialize_kg",
    "serialize_chunks",
    "serialize_kg",
]
