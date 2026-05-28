# =============================================================================
# File: c5_tools.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c9_mcp/tools/c5_tools.py
# Description: C9 tool adapters wrapping the C5 Requirements Service facade.
#              v1 scope is READ-ONLY (see S-2 ratified 2026-04-23): write
#              operations through MCP are a distinct security surface and
#              deferred to v2.
#
# @relation implements:R-100-015
# @relation uses:E-100-002
# =============================================================================

from __future__ import annotations

from typing import Any

from ay_platform_core.c5_requirements.models import RelationType, RequirementStatus
from ay_platform_core.c5_requirements.service import RequirementsService
from ay_platform_core.c9_mcp.tools.base import Tool, ToolDispatchError


def build_tools(c5: RequirementsService) -> list[Tool]:
    """Return the full list of C5-backed tools."""
    return [
        _list_entities_tool(c5),
        _get_entity_tool(c5),
        _list_documents_tool(c5),
        _get_document_tool(c5),
        _list_relations_tool(c5),
    ]


# ---------------------------------------------------------------------------
# c5_list_entities
# ---------------------------------------------------------------------------


def _list_entities_tool(c5: RequirementsService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_str(args, "project_id")
        limit = _optional_int(args, "limit", default=50, low=1, high=500)
        status_raw = args.get("status")
        category = args.get("category")
        domain = args.get("domain")
        text = args.get("text")
        status_filter: RequirementStatus | None = None
        if status_raw is not None:
            try:
                status_filter = RequirementStatus(status_raw)
            except ValueError as exc:
                raise ToolDispatchError(
                    f"invalid status value: {status_raw!r}"
                ) from exc

        entities, next_cursor = await c5.list_entities(
            project_id,
            limit=limit,
            cursor=args.get("cursor"),
            status_filter=status_filter,
            category_filter=category,
            domain_filter=domain,
            text_filter=text,
        )
        return {
            "entities": [e.model_dump(mode="json") for e in entities],
            "next_cursor": next_cursor,
        }

    return Tool(
        name="c5_list_entities",
        description=(
            "List requirement entities of a project. Optional filters by "
            "status, category, domain, text. Returns paginated results."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "cursor": {"type": "string"},
                "status": {"type": "string"},
                "category": {"type": "string"},
                "domain": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["project_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# c5_get_entity
# ---------------------------------------------------------------------------


def _get_entity_tool(c5: RequirementsService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_str(args, "project_id")
        entity_id = _required_str(args, "entity_id")
        entity = await c5.get_entity(project_id, entity_id)
        return entity.model_dump(mode="json")

    return Tool(
        name="c5_get_entity",
        description="Fetch a single requirement entity by id.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "entity_id": {"type": "string"},
            },
            "required": ["project_id", "entity_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# c5_list_documents
# ---------------------------------------------------------------------------


def _list_documents_tool(c5: RequirementsService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_str(args, "project_id")
        limit = _optional_int(args, "limit", default=50, low=1, high=500)
        docs, next_cursor = await c5.list_documents(
            project_id, limit=limit, cursor=args.get("cursor")
        )
        return {
            "documents": [d.model_dump(mode="json") for d in docs],
            "next_cursor": next_cursor,
        }

    return Tool(
        name="c5_list_documents",
        description="List requirement documents of a project.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "cursor": {"type": "string"},
            },
            "required": ["project_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# c5_get_document
# ---------------------------------------------------------------------------


def _get_document_tool(c5: RequirementsService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_str(args, "project_id")
        slug = _required_str(args, "slug")
        document = await c5.get_document(project_id, slug)
        return document.model_dump(mode="json")

    return Tool(
        name="c5_get_document",
        description="Fetch a single requirement document including body markdown.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "slug": {"type": "string"},
            },
            "required": ["project_id", "slug"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# c5_list_relations
# ---------------------------------------------------------------------------


def _list_relations_tool(c5: RequirementsService) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        project_id = _required_str(args, "project_id")
        source_id = _required_str(args, "source_id")
        rel_type_raw = args.get("type")
        rel_type: RelationType | None = None
        if rel_type_raw is not None:
            try:
                rel_type = RelationType(rel_type_raw)
            except ValueError as exc:
                raise ToolDispatchError(
                    f"invalid relation type: {rel_type_raw!r}"
                ) from exc
        edges = await c5.list_relations(project_id, source_id, rel_type)
        return {"relations": [e.model_dump(mode="json") for e in edges]}

    return Tool(
        name="c5_list_relations",
        description=(
            "List relation edges outgoing from a source entity. Optionally "
            "filter by relation type (derives-from, impacts, tailoring-of, "
            "supersedes, superseded-by)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "source_id": {"type": "string"},
                "type": {"type": "string"},
            },
            "required": ["project_id", "source_id"],
        },
        handler=handler,
    )


# ---------------------------------------------------------------------------
# Arg helpers (strict — MCP callers are external agents; don't trust the input)
# ---------------------------------------------------------------------------


def _required_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ToolDispatchError(f"missing or invalid required argument: {key}")
    return value


def _optional_int(
    args: dict[str, Any], key: str, *, default: int, low: int, high: int
) -> int:
    raw: Any = args.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolDispatchError(f"argument {key!r} must be an integer")
    value: int = raw
    if value < low or value > high:
        raise ToolDispatchError(
            f"argument {key!r} must be in [{low}, {high}]"
        )
    return value
