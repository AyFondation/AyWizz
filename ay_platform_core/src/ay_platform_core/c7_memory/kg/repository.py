# =============================================================================
# File: repository.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c7_memory/kg/repository.py
# Description: ArangoDB persistence for the knowledge graph extracted by
#              `extractor.py` (Phase F.1 of v1 plan). Two collections:
#                - `memory_kg_entities` (vertex) — one row per
#                  (tenant_id, project_id, normalised entity name, type).
#                  Multiple sources mentioning the same entity converge.
#                - `memory_kg_relations` (edge) — directed `_from` →
#                  `_to`, attributed with the source(s) that mentioned
#                  the relation.
#
#              v2 (Phase F.2): adds `find_neighbor_source_ids` —
#              given a set of seed source_ids, find entities mentioning
#              them, traverse 1-hop on the edge collection, return the
#              source_ids of the neighbour entities. Used by
#              `MemoryService.retrieve` to expand the candidate pool
#              with graph-related sources beyond the vector scan window.
#
#              v4 (V2 #3-A.a): `persist_structural` — schema-guided L1 records
#              into the same collections + `layer`/`ontology_version` columns.
#              v5 (V2 #3-C, D-019 / R-400-209): bi-temporal KG — temporal
#              columns (valid_from/valid_to/recorded_at/superseded_at) on
#              persisted records ; `supersede_relation` (append-only
#              correction : close current versions, insert a new one) ;
#              `relations_as_of` (valid_at / known_as_of filtering).
#
# @relation implements:R-400-200
# @relation implements:R-400-209
# =============================================================================

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from arango.cursor import Cursor
from arango.database import StandardDatabase

from ay_platform_core.c7_memory.kg.ontology import StructuralExtraction
from ay_platform_core.c7_memory.models import KGEntity, KGRelation

COLL_ENTITIES = "memory_kg_entities"
COLL_RELATIONS = "memory_kg_relations"


_ALLOWED_KEY_CHARS = re.compile(r"[^A-Za-z0-9_\-]")


def _sanitize_key_segment(value: str, *, max_len: int) -> str:
    """ArangoDB _key allows `[A-Za-z0-9_-:.@()+,=;$!*'%]`; lowest-common-
    denominator we keep alphanum + underscore + hyphen. Everything else
    becomes underscore. Trim to `max_len`."""
    cleaned = _ALLOWED_KEY_CHARS.sub("_", value.strip().lower())
    return cleaned[:max_len] or "_"


def _entity_key_from(
    tenant_id: str, project_id: str, name: str, type_: str
) -> str:
    """Composite key from raw name/type. Lowercased + sanitised so case- or
    whitespace-only variants converge. Tenant + project scope strict.
    Shared by the open-domain (`KGEntity`) and structural (`StructuralEntity`)
    persistence paths."""
    safe_tenant = _sanitize_key_segment(tenant_id, max_len=32)
    safe_project = _sanitize_key_segment(project_id, max_len=32)
    safe_type = _sanitize_key_segment(type_, max_len=32)
    safe_name = _sanitize_key_segment(name, max_len=64)
    return f"{safe_tenant}-{safe_project}-{safe_type}-{safe_name}"


def _entity_key(tenant_id: str, project_id: str, entity: KGEntity) -> str:
    return _entity_key_from(tenant_id, project_id, entity.name, entity.type)


def _iso(value: datetime | None) -> str | None:
    """Datetime → ISO-8601 string (UTC-stamped strings compare chronologically
    as lexicographic strings, which the as-of AQL filters rely on)."""
    return value.isoformat() if value is not None else None


def _relation_key(
    tenant_id: str,
    project_id: str,
    subj_key: str,
    rel: str,
    obj_key: str,
) -> str:
    safe_tenant = _sanitize_key_segment(tenant_id, max_len=32)
    safe_project = _sanitize_key_segment(project_id, max_len=32)
    safe_rel = _sanitize_key_segment(rel, max_len=64)
    # Subject + object keys are already sanitised; we keep them as-is
    # (no second pass) and stitch with `__` separators.
    return f"{safe_tenant}-{safe_project}-{subj_key}__{safe_rel}__{obj_key}"


class KGRepository:
    """Async wrapper around python-arango (sync) for the KG collections."""

    def __init__(self, db: StandardDatabase) -> None:
        self._db = db

    def _ensure_collections_sync(self) -> None:
        if not self._db.has_collection(COLL_ENTITIES):
            self._db.create_collection(COLL_ENTITIES)
        if not self._db.has_collection(COLL_RELATIONS):
            self._db.create_collection(COLL_RELATIONS, edge=True)

    async def ensure_collections(self) -> None:
        await asyncio.to_thread(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Persist a batch of entities + relations atomically per call.
    # ------------------------------------------------------------------

    def _persist_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        entities: list[KGEntity],
        relations: list[KGRelation],
    ) -> tuple[int, int]:
        now = datetime.now(UTC).isoformat()
        ent_coll = self._db.collection(COLL_ENTITIES)
        rel_coll = self._db.collection(COLL_RELATIONS)

        # Build entity docs first so we can resolve relation `_from`/`_to`
        # by composite key (entities mentioned in relations may or may
        # not appear in the standalone `entities` list — be lenient).
        seen_keys: dict[tuple[str, str], str] = {}

        def _upsert_entity(entity: KGEntity) -> str:
            key = _entity_key(tenant_id, project_id, entity)
            if (entity.name, entity.type) in seen_keys:
                return seen_keys[(entity.name, entity.type)]
            doc = {
                "_key": key,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "name": entity.name,
                "type": entity.type,
                "source_ids": [source_id],
                "first_seen_at": now,
                "last_seen_at": now,
                # Provenance + confidence (R-400-201) so the graph is honest
                # about extracted-vs-inferred and queryable by the lint pass.
                "provenance": entity.provenance.value,
                "confidence": entity.confidence,
            }
            existing = cast("dict[str, Any] | None", ent_coll.get(key))
            if existing is None:
                ent_coll.insert(doc)
            else:
                # Merge source provenance + bump last_seen_at without
                # dropping older sources.
                src_ids = list(existing.get("source_ids", []))
                if source_id not in src_ids:
                    src_ids.append(source_id)
                ent_coll.update({
                    "_key": key,
                    "source_ids": src_ids,
                    "last_seen_at": now,
                })
            seen_keys[(entity.name, entity.type)] = key
            return key

        added_entities = 0
        for entity in entities:
            key = _upsert_entity(entity)
            after = cast("dict[str, Any]", ent_coll.get(key))
            if after.get("source_ids") == [source_id]:
                added_entities += 1

        added_relations = 0
        for rel in relations:
            subj_key = _upsert_entity(rel.subject)
            obj_key = _upsert_entity(rel.object)
            edge_key = _relation_key(
                tenant_id, project_id, subj_key, rel.relation, obj_key,
            )
            edge_doc = {
                "_key": edge_key,
                "_from": f"{COLL_ENTITIES}/{subj_key}",
                "_to": f"{COLL_ENTITIES}/{obj_key}",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "relation": rel.relation,
                "source_id": source_id,
                "created_at": now,
                # Provenance + confidence (R-400-201) on the edge.
                "provenance": rel.provenance.value,
                "confidence": rel.confidence,
            }
            if not rel_coll.has(edge_key):
                rel_coll.insert(edge_doc)
                added_relations += 1
        return added_entities, added_relations

    async def persist(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        entities: list[KGEntity],
        relations: list[KGRelation],
    ) -> tuple[int, int]:
        return await asyncio.to_thread(
            self._persist_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            entities=entities,
            relations=relations,
        )

    # ------------------------------------------------------------------
    # Schema-guided L1 structural persistence (V2 #3-A.a / R-400-200).
    # Same two collections as the open-domain path, plus `layer` and
    # `ontology_version` columns so the v2 layered graph (R-400-205) and
    # replay/versioning (R-400-207) are additive — no separate store.
    # ------------------------------------------------------------------

    def _persist_structural_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        extraction: StructuralExtraction,
    ) -> tuple[int, int]:
        now = datetime.now(UTC).isoformat()
        ent_coll = self._db.collection(COLL_ENTITIES)
        rel_coll = self._db.collection(COLL_RELATIONS)
        seen_keys: set[str] = set()

        def _upsert(
            name: str, type_: str, layer: str, ont_v: int,
            valid_from: str | None = None, valid_to: str | None = None,
        ) -> tuple[str, bool]:
            """Returns (key, inserted) — `inserted` True only on a genuine
            first insert, so the caller can count NEW rows accurately
            (idempotent re-run → 0 added)."""
            key = _entity_key_from(tenant_id, project_id, name, type_)
            if key in seen_keys:
                return key, False
            doc = {
                "_key": key,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "name": name,
                "type": type_,
                "source_ids": [source_id],
                "first_seen_at": now,
                "last_seen_at": now,
                # Deterministic structural extraction is EXTRACTED / 1.0
                # (R-400-201) ; layer + ontology_version are forward-compat
                # for the v2 layered graph and replay (R-400-205/207).
                "provenance": "extracted",
                "confidence": 1.0,
                "layer": layer,
                "ontology_version": ont_v,
                # Bi-temporal columns (D-019 / R-400-209). valid_* from the
                # model (null = timeless) ; recorded_at = transaction-time
                # start ; superseded_at = null (this is the current version).
                "valid_from": valid_from,
                "valid_to": valid_to,
                "recorded_at": now,
                "superseded_at": None,
            }
            existing = cast("dict[str, Any] | None", ent_coll.get(key))
            if existing is None:
                ent_coll.insert(doc)
                inserted = True
            else:
                src_ids = list(existing.get("source_ids", []))
                if source_id not in src_ids:
                    src_ids.append(source_id)
                ent_coll.update({
                    "_key": key,
                    "source_ids": src_ids,
                    "last_seen_at": now,
                    "layer": layer,
                    "ontology_version": ont_v,
                })
                inserted = False
            seen_keys.add(key)
            return key, inserted

        added_entities = 0
        for ent in extraction.entities:
            _key, inserted = _upsert(
                ent.name, ent.type, ent.layer, ent.ontology_version,
                _iso(ent.valid_from), _iso(ent.valid_to),
            )
            if inserted:
                added_entities += 1

        added_relations = 0
        for rel in extraction.relations:
            subj_key, subj_inserted = _upsert(
                rel.subject.name, rel.subject.type,
                rel.subject.layer, rel.subject.ontology_version,
                _iso(rel.subject.valid_from), _iso(rel.subject.valid_to),
            )
            obj_key, obj_inserted = _upsert(
                rel.object.name, rel.object.type,
                rel.object.layer, rel.object.ontology_version,
                _iso(rel.object.valid_from), _iso(rel.object.valid_to),
            )
            # An entity first seen as a relation endpoint still counts as new.
            added_entities += int(subj_inserted) + int(obj_inserted)
            edge_key = _relation_key(
                tenant_id, project_id, subj_key, rel.type, obj_key,
            )
            if not rel_coll.has(edge_key):
                rel_coll.insert({
                    "_key": edge_key,
                    "_from": f"{COLL_ENTITIES}/{subj_key}",
                    "_to": f"{COLL_ENTITIES}/{obj_key}",
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "relation": rel.type,
                    "source_id": source_id,
                    "created_at": now,
                    "provenance": "extracted",
                    "confidence": 1.0,
                    "layer": rel.layer,
                    "ontology_version": rel.ontology_version,
                    # Bi-temporal columns (D-019 / R-400-209).
                    "valid_from": _iso(rel.valid_from),
                    "valid_to": _iso(rel.valid_to),
                    "recorded_at": now,
                    "superseded_at": None,
                })
                added_relations += 1
        return added_entities, added_relations

    async def persist_structural(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        extraction: StructuralExtraction,
    ) -> tuple[int, int]:
        """Persist a deterministic L1 structural extraction. Idempotent —
        the composite entity key and the (subj, type, obj) edge key make a
        re-run a no-op. Returns (entities_added, relations_added)."""
        return await asyncio.to_thread(
            self._persist_structural_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            source_id=source_id,
            extraction=extraction,
        )

    # ------------------------------------------------------------------
    # Bi-temporal supersession + as-of queries (D-019 / R-400-209).
    # ------------------------------------------------------------------

    def _supersede_relation_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_name: str,
        subject_type: str,
        relation: str,
        object_name: str,
        object_type: str,
        valid_from: str | None,
        source_id: str,
    ) -> str:
        """Append-only correction of a logical relation. Closes every CURRENT
        (transaction-open) version's transaction interval, then inserts a new
        versioned row. Returns the new row's `_key`. Nothing is deleted."""
        now = datetime.now(UTC).isoformat()
        rel_coll = self._db.collection(COLL_RELATIONS)
        subj_key = _entity_key_from(tenant_id, project_id, subject_name, subject_type)
        obj_key = _entity_key_from(tenant_id, project_id, object_name, object_type)
        from_ref = f"{COLL_ENTITIES}/{subj_key}"
        to_ref = f"{COLL_ENTITIES}/{obj_key}"

        # Close the transaction interval of any open version (and close an open
        # valid interval at the same instant).
        self._db.aql.execute(
            "FOR r IN memory_kg_relations "
            "FILTER r.tenant_id == @tid AND r.project_id == @pid "
            "AND r._from == @from AND r._to == @to AND r.relation == @rel "
            "AND r.superseded_at == null "
            "UPDATE r WITH { superseded_at: @now, "
            "valid_to: (r.valid_to == null ? @now : r.valid_to) } "
            "IN memory_kg_relations",
            bind_vars={
                "tid": tenant_id, "pid": project_id, "from": from_ref,
                "to": to_ref, "rel": relation, "now": now,
            },
        )
        base = _relation_key(tenant_id, project_id, subj_key, relation, obj_key)
        new_key = f"{base}@{uuid.uuid4().hex[:12]}"
        rel_coll.insert({
            "_key": new_key,
            "_from": from_ref,
            "_to": to_ref,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "relation": relation,
            "source_id": source_id,
            "created_at": now,
            "provenance": "extracted",
            "confidence": 1.0,
            "layer": "L1",
            "ontology_version": 1,
            "valid_from": valid_from,
            "valid_to": None,
            "recorded_at": now,
            "superseded_at": None,
        })
        return new_key

    async def supersede_relation(
        self,
        *,
        tenant_id: str,
        project_id: str,
        subject_name: str,
        subject_type: str,
        relation: str,
        object_name: str,
        object_type: str,
        valid_from: datetime | None,
        source_id: str,
    ) -> str:
        """Bi-temporal append-only correction (D-019). Supersedes the current
        version(s) of a logical relation and records a new one."""
        return await asyncio.to_thread(
            self._supersede_relation_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            subject_name=subject_name,
            subject_type=subject_type,
            relation=relation,
            object_name=object_name,
            object_type=object_type,
            valid_from=_iso(valid_from),
            source_id=source_id,
        )

    def _relations_as_of_sync(
        self,
        *,
        tenant_id: str,
        project_id: str,
        valid_at: str | None,
        known_as_of: str | None,
    ) -> list[dict[str, Any]]:
        # ISO-8601 UTC strings compare chronologically as strings. A null
        # interval bound is open (always matches). `known_as_of` filters the
        # transaction axis ; `valid_at` the valid axis. Both optional.
        cursor = cast("Cursor", self._db.aql.execute(
            "FOR r IN memory_kg_relations "
            "FILTER r.tenant_id == @tid AND r.project_id == @pid "
            "AND (@ka == null OR (r.recorded_at <= @ka "
            "    AND (r.superseded_at == null OR r.superseded_at > @ka))) "
            "AND (@va == null OR ((r.valid_from == null OR r.valid_from <= @va) "
            "    AND (r.valid_to == null OR r.valid_to > @va))) "
            "LET s = DOCUMENT(r._from) LET o = DOCUMENT(r._to) "
            "RETURN {subject: s.name, relation: r.relation, object: o.name, "
            "valid_from: r.valid_from, valid_to: r.valid_to, "
            "recorded_at: r.recorded_at, superseded_at: r.superseded_at}",
            bind_vars={
                "tid": tenant_id, "pid": project_id,
                "ka": known_as_of, "va": valid_at,
            },
        ))
        return list(cursor)

    async def relations_as_of(
        self,
        *,
        tenant_id: str,
        project_id: str,
        valid_at: datetime | None = None,
        known_as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """As-of query (D-019 / R-400-209). `valid_at` = facts true at that
        world-time ; `known_as_of` = facts the system asserted at that
        transaction-time. Either/both/neither (neither → all rows)."""
        return await asyncio.to_thread(
            self._relations_as_of_sync,
            tenant_id=tenant_id,
            project_id=project_id,
            valid_at=_iso(valid_at),
            known_as_of=_iso(known_as_of),
        )

    # ------------------------------------------------------------------
    # Inspection (used by tests; useful for admin tooling later).
    # ------------------------------------------------------------------

    def _list_entities_for_source_sync(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> list[dict[str, Any]]:
        cursor = cast("Cursor", self._db.aql.execute(
            "FOR e IN memory_kg_entities "
            "FILTER e.tenant_id == @tid AND e.project_id == @pid "
            "AND @sid IN e.source_ids "
            "RETURN e",
            bind_vars={"tid": tenant_id, "pid": project_id, "sid": source_id},
        ))
        return list(cursor)

    async def list_entities_for_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_entities_for_source_sync, tenant_id, project_id, source_id,
        )

    def _list_relations_for_source_sync(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> list[dict[str, Any]]:
        cursor = cast("Cursor", self._db.aql.execute(
            "FOR r IN memory_kg_relations "
            "FILTER r.tenant_id == @tid AND r.project_id == @pid "
            "AND r.source_id == @sid "
            "RETURN r",
            bind_vars={"tid": tenant_id, "pid": project_id, "sid": source_id},
        ))
        return list(cursor)

    async def list_relations_for_source(
        self, tenant_id: str, project_id: str, source_id: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_relations_for_source_sync, tenant_id, project_id, source_id,
        )

    # ------------------------------------------------------------------
    # Project-level summary — a simple graph "bootstrap" inspection view.
    # ------------------------------------------------------------------

    def _summary_sync(
        self, tenant_id: str, project_id: str, *, sample_limit: int
    ) -> dict[str, Any]:
        bind: dict[str, Any] = {"t": tenant_id, "p": project_id}
        ent_count = next(iter(cast("Cursor", self._db.aql.execute(
            "RETURN LENGTH(FOR e IN memory_kg_entities "
            "FILTER e.tenant_id == @t AND e.project_id == @p RETURN 1)",
            bind_vars=bind,
        ))))
        rel_count = next(iter(cast("Cursor", self._db.aql.execute(
            "RETURN LENGTH(FOR r IN memory_kg_relations "
            "FILTER r.tenant_id == @t AND r.project_id == @p RETURN 1)",
            bind_vars=bind,
        ))))
        sample_bind: dict[str, Any] = {**bind, "n": sample_limit}
        sample = list(cast("Cursor", self._db.aql.execute(
            "FOR r IN memory_kg_relations "
            "FILTER r.tenant_id == @t AND r.project_id == @p "
            "LIMIT @n "
            "LET s = DOCUMENT(r._from) LET o = DOCUMENT(r._to) "
            "RETURN {subject: s.name, relation: r.relation, object: o.name, "
            "provenance: r.provenance, confidence: r.confidence}",
            bind_vars=sample_bind,
        )))
        return {
            "entity_count": int(ent_count),
            "relation_count": int(rel_count),
            "sample": sample,
        }

    async def summary(
        self, tenant_id: str, project_id: str, *, sample_limit: int = 10
    ) -> dict[str, Any]:
        """Counts + a small sample of relation triples (with provenance) for
        a project's knowledge graph — a lightweight inspection view."""
        return await asyncio.to_thread(
            self._summary_sync, tenant_id, project_id, sample_limit=sample_limit,
        )

    # ------------------------------------------------------------------
    # Phase F.2 — graph expansion at retrieve time.
    # ------------------------------------------------------------------

    def _find_neighbor_source_ids_sync(
        self,
        tenant_id: str,
        project_id: str,
        seed_source_ids: list[str],
        depth: int,
    ) -> list[str]:
        if not seed_source_ids or depth < 1:
            return []
        # Two-stage AQL:
        #   1. seed_entities = entities mentioning ANY seed source_id;
        #   2. ANY-direction 1..depth traversal returns neighbours
        #      (excluding the seed entities themselves via the path
        #      vertex predicate). The traversal is `ANY` because the
        #      semantic relevance of "graph proximity" is direction-
        #      agnostic — if A is "discovered_by" B, we want both
        #      directions of expansion.
        aql = """
        LET seeds = (
            FOR e IN memory_kg_entities
                FILTER e.tenant_id == @tid AND e.project_id == @pid
                FILTER LENGTH(INTERSECTION(e.source_ids, @sids)) > 0
                RETURN e
        )
        LET seed_keys = (FOR s IN seeds RETURN s._key)
        LET neighbour_source_ids = (
            FOR seed IN seeds
                FOR v IN 1..@depth ANY seed memory_kg_relations
                    FILTER v.tenant_id == @tid AND v.project_id == @pid
                    FILTER v._key NOT IN seed_keys
                    FOR sid IN v.source_ids
                        RETURN DISTINCT sid
        )
        RETURN neighbour_source_ids
        """
        bind_vars: dict[str, Any] = {
            "tid": tenant_id,
            "pid": project_id,
            "sids": seed_source_ids,
            "depth": depth,
        }
        cursor = cast(
            "Cursor", self._db.aql.execute(aql, bind_vars=bind_vars),
        )
        rows = list(cursor)
        if not rows:
            return []
        # The cursor yields a single row (the wrapping list comprehension).
        return [str(s) for s in rows[0]]

    async def find_neighbor_source_ids(
        self,
        tenant_id: str,
        project_id: str,
        seed_source_ids: list[str],
        depth: int = 1,
    ) -> list[str]:
        """Given a set of seed source_ids, return the source_ids of
        entities reachable within `depth` hops on the relation edge
        collection. Seed source_ids are excluded from the result —
        only NEW source provenance is returned. Result is unordered;
        callers SHALL apply their own cap if they want a bound."""
        return await asyncio.to_thread(
            self._find_neighbor_source_ids_sync,
            tenant_id,
            project_id,
            seed_source_ids,
            depth,
        )
