# =============================================================================
# File: repository.py
# Version: 4
# Path: ay_platform_core/src/ay_platform_core/c3_conversation/db/repository.py
# Description: ArangoDB repository for C3 — conversations and messages.
#              All public methods are async (asyncio.to_thread wrapper over
#              python-arango sync driver). Collections: c3_conversations,
#              c3_messages.
#              soft-delete: conversations get deleted=True, never physically
#              removed. Messages are not deleted when a conversation is deleted.
#              v4: append_message persists a single unified `events`
#                  list (the inline-activity audit ledger : pipeline
#                  stages + DocGen tool calls + future kinds) replacing
#                  the v3 per-kind `stages` field. Legacy docs keep
#                  their `stages` field ; the C3 service read path
#                  projects them into `events` (no data migration).
#              v3: append_message accepted an optional `stages` list
#                  (superseded by v4's unified `events`).
#              v2: _append_message_sync AQL rewritten — OLD is not bound in
#                  WITH of `UPDATE @key`; use LET doc = DOCUMENT(...) to read
#                  the current message_count before the UPDATE.
# @relation R-100-003 R-100-006
# =============================================================================

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from arango import ArangoClient  # type: ignore[attr-defined]

COLL_CONVERSATIONS = "c3_conversations"
COLL_MESSAGES = "c3_messages"


class ConversationRepository:
    """Sync ArangoDB operations wrapped for async use via asyncio.to_thread."""

    def __init__(self, db: Any) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Collection bootstrap (idempotent)
    # ------------------------------------------------------------------

    def _ensure_collections_sync(self) -> None:
        existing = {c["name"] for c in self._db.collections()}
        for name in (COLL_CONVERSATIONS, COLL_MESSAGES):
            if name not in existing:
                self._db.create_collection(name)

    async def ensure_collections(self) -> None:
        await asyncio.to_thread(self._ensure_collections_sync)

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------

    def _create_conversation_sync(
        self,
        owner_id: str,
        title: str,
        project_id: str | None,
    ) -> dict[str, Any]:
        conv_id = uuid4()
        now = datetime.now(UTC).isoformat()
        doc = {
            "_key": str(conv_id),
            "id": str(conv_id),
            "owner_id": owner_id,
            "project_id": project_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
            "deleted": False,
        }
        self._db.collection(COLL_CONVERSATIONS).insert(doc)
        return doc

    async def create_conversation(
        self,
        owner_id: str,
        title: str,
        project_id: str | None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_conversation_sync, owner_id, title, project_id
        )

    def _get_conversation_sync(self, conversation_id: UUID) -> dict[str, Any] | None:
        doc = cast(
            dict[str, Any] | None,
            self._db.collection(COLL_CONVERSATIONS).get(str(conversation_id)),
        )
        if doc is None or doc.get("deleted"):
            return None
        return doc

    async def get_conversation(self, conversation_id: UUID) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_conversation_sync, conversation_id)

    def _list_conversations_sync(self, owner_id: str) -> list[dict[str, Any]]:
        aql = """
        FOR c IN c3_conversations
            FILTER c.owner_id == @owner_id AND c.deleted == false
            SORT c.updated_at DESC
            RETURN c
        """
        cursor = self._db.aql.execute(aql, bind_vars={"owner_id": owner_id})
        return cast(list[dict[str, Any]], list(cursor))

    async def list_conversations(self, owner_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_conversations_sync, owner_id)

    def _update_conversation_sync(
        self,
        conversation_id: UUID,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        updates["updated_at"] = datetime.now(UTC).isoformat()
        result = self._db.collection(COLL_CONVERSATIONS).update(
            {"_key": str(conversation_id), **updates},
            return_new=True,
        )
        return cast(dict[str, Any] | None, result["new"])

    async def update_conversation(
        self,
        conversation_id: UUID,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._update_conversation_sync, conversation_id, updates
        )

    def _soft_delete_conversation_sync(self, conversation_id: UUID) -> None:
        self._db.collection(COLL_CONVERSATIONS).update(
            {"_key": str(conversation_id), "deleted": True}
        )

    async def soft_delete_conversation(self, conversation_id: UUID) -> None:
        await asyncio.to_thread(self._soft_delete_conversation_sync, conversation_id)

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def _append_message_sync(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        events: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        msg_id = uuid4()
        now = datetime.now(UTC).isoformat()
        doc: dict[str, Any] = {
            "_key": str(msg_id),
            "id": str(msg_id),
            "conversation_id": str(conversation_id),
            "role": role,
            "content": content,
            "timestamp": now,
        }
        # Only persist `events` when the caller supplied a non-empty
        # list. Absent field == no inline activity (user message /
        # non-DocGen non-pipeline turn) ; the UX renders the bubble
        # with no inline log. Legacy messages carry the pre-unification
        # `stages` field instead — the read path projects those into
        # `events` so no migration is needed.
        if events:
            doc["events"] = events
        # Tranche B (R-200-182) : prompt-attached references — metadata
        # only (path, kind, range), NEVER the resolved content.
        if references:
            doc["references"] = references
        self._db.collection(COLL_MESSAGES).insert(doc)
        # Increment message_count on the parent conversation. OLD is not bound
        # in the WITH clause of `UPDATE @key`; bind the current document via
        # LET/DOCUMENT and read message_count from it instead.
        self._db.aql.execute(
            """
            LET doc = DOCUMENT('c3_conversations', @key)
            UPDATE doc WITH {
                message_count: doc.message_count + 1,
                updated_at: @now
            } IN c3_conversations
            """,
            bind_vars={"key": str(conversation_id), "now": now},
        )
        return doc

    async def append_message(
        self,
        conversation_id: UUID,
        role: str,
        content: str,
        events: list[dict[str, Any]] | None = None,
        references: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._append_message_sync,
            conversation_id,
            role,
            content,
            events,
            references,
        )

    def _list_messages_sync(self, conversation_id: UUID) -> list[dict[str, Any]]:
        aql = """
        FOR m IN c3_messages
            FILTER m.conversation_id == @conv_id
            SORT m.timestamp ASC
            RETURN m
        """
        cursor = self._db.aql.execute(
            aql, bind_vars={"conv_id": str(conversation_id)}
        )
        return cast(list[dict[str, Any]], list(cursor))

    async def list_messages(self, conversation_id: UUID) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_messages_sync, conversation_id)


def make_repository(
    host: str, port: int, username: str, password: str, db_name: str
) -> ConversationRepository:
    """Factory used by the FastAPI lifespan and integration tests."""
    client = ArangoClient(hosts=f"http://{host}:{port}")
    db = client.db(db_name, username=username, password=password)
    return ConversationRepository(db)
