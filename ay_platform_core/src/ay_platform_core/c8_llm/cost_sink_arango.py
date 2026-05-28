# =============================================================================
# File: cost_sink_arango.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c8_llm/cost_sink_arango.py
# Description: ArangoDB-backed `CallRecordSink` (R-800-070) — persists one
#              `llm_calls` document per recorded LLM call (E-800-002). The
#              C8 cost receiver injects this into `build_call_record` ; unit
#              tests use an in-memory sink instead. python-arango is
#              synchronous, so writes go through `asyncio.to_thread`
#              (the established C2/C3 pattern).
#
# @relation implements:R-800-070
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

from ay_platform_core.c8_llm.models import CallRecord

COLLECTION = "llm_calls"


class ArangoCallRecordSink:
    """Writes `CallRecord`s to the `llm_calls` collection. `call_id` is
    the document `_key`, so a re-delivered envelope (the forwarder is
    best-effort / at-least-once) is idempotent on retry only when the
    same call_id is reused — here each receive mints a fresh call_id, so
    duplicates are tolerated as distinct rows (cost double-count risk is
    accepted for v1 ; dedup by fingerprint is a v2 concern)."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def ensure_collection(self) -> None:
        """Idempotent — create `llm_calls` if absent. Called once at
        receiver startup."""
        if not self._db.has_collection(COLLECTION):
            self._db.create_collection(COLLECTION)

    async def insert(self, record: CallRecord) -> None:
        await asyncio.to_thread(self._insert_sync, record)

    def _insert_sync(self, record: CallRecord) -> None:
        doc = record.model_dump(mode="json")
        doc["_key"] = record.call_id
        self._db.collection(COLLECTION).insert(doc)
