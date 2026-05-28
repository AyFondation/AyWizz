# =============================================================================
# File: extractor.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/kg/extractor.py
# Description: LLM-based entity + relation extractor for Phase F.1 of
#              the v1 functional plan. Issues a structured prompt to
#              the C8 gateway and parses a strict JSON response.
#
#              Determinism: tests use a `ScriptedLLM` that returns
#              canned JSON; the extractor SHALL parse it identically
#              to a real provider response (we don't depend on
#              vendor-specific extensions).
# =============================================================================

from __future__ import annotations

import json
import re
from typing import Any

from ay_platform_core.c7_memory.models import KGEntity, KGRelation
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)

# Truncate long sources so the prompt fits in a typical 8k context.
# Operators with bigger models can override via the chunked-extraction
# pattern (split + merge) — out of scope for v1.1.
_MAX_PROMPT_CHARS = 6000

_SYSTEM_PROMPT = (
    "You are an information extractor. From the given source text, "
    "extract:\n"
    "  - named ENTITIES (people, organizations, places, products, "
    "technical concepts) with a short type label;\n"
    "  - directed RELATIONS between entities, in subject-relation-object "
    "form, only when the text explicitly states or directly implies them.\n\n"
    "Return STRICT JSON matching this shape, with NO surrounding text:\n"
    "{\n"
    '  "entities": [{"name": "...", "type": "..."}, ...],\n'
    '  "relations": [\n'
    '    {"subject": {"name": "...", "type": "..."}, '
    '"relation": "...", '
    '"object": {"name": "...", "type": "..."}}, ...\n'
    "  ]\n"
    "}\n\n"
    "Use lowercase for `type` and `relation`. Use the entity's natural "
    'casing for `name`. If no entities are present, return '
    '{"entities": [], "relations": []}.'
)


class KGExtractionError(RuntimeError):
    """Raised when the LLM response cannot be parsed into the expected
    shape. The caller (service) maps this to a user-facing 502."""


def _strip_code_fence(text: str) -> str:
    """LLMs occasionally wrap JSON in a ```json fence. Strip it."""
    fenced = re.match(
        r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL,
    )
    if fenced:
        return fenced.group(1)
    return text.strip()


def _parse_response(payload: str) -> tuple[list[KGEntity], list[KGRelation]]:
    """Parse the LLM response into typed lists. Lenient on missing
    fields; strict on shape mismatches."""
    cleaned = _strip_code_fence(payload)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise KGExtractionError(
            f"LLM returned non-JSON response: {exc.msg} at offset {exc.pos}"
        ) from exc
    if not isinstance(obj, dict):
        raise KGExtractionError("LLM response must be a JSON object")

    raw_entities = obj.get("entities", [])
    raw_relations = obj.get("relations", [])
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        raise KGExtractionError(
            "LLM response must contain `entities` and `relations` lists"
        )

    entities: list[KGEntity] = []
    for raw in raw_entities:
        if not isinstance(raw, dict):
            continue
        try:
            entities.append(KGEntity.model_validate(raw))
        except Exception:
            # Skip malformed entries rather than fail the whole batch.
            continue

    relations: list[KGRelation] = []
    for raw in raw_relations:
        if not isinstance(raw, dict):
            continue
        try:
            relations.append(KGRelation.model_validate(raw))
        except Exception:
            continue

    return entities, relations


async def extract_entities_and_relations(
    *,
    llm_client: LLMGatewayClient,
    source_text: str,
    tenant_id: str,
    project_id: str,
    source_id: str,
) -> tuple[list[KGEntity], list[KGRelation]]:
    """Call the LLM gateway and parse the structured response.

    Returns `([], [])` when the source text is empty after truncation.
    Raises `KGExtractionError` when the LLM response cannot be parsed
    as the expected shape.
    """
    text = source_text.strip()
    if not text:
        return [], []
    if len(text) > _MAX_PROMPT_CHARS:
        text = text[:_MAX_PROMPT_CHARS] + "…"

    request = ChatCompletionRequest(
        messages=[
            ChatMessage(role=ChatRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=ChatRole.USER, content=text),
        ],
        stream=False,
    )
    response = await llm_client.chat_completion(
        request,
        agent_name="c7-kg-extractor",
        session_id=f"kg:{source_id}",
        tenant_id=tenant_id,
        project_id=project_id,
    )
    if not response.choices:
        raise KGExtractionError("LLM response had no choices")
    content = response.choices[0].message.content
    if isinstance(content, list):
        # OpenAI multi-modal shape — concat the text parts.
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(content, str):
        raise KGExtractionError("LLM response content was not text")
    return _parse_response(content)


__all__ = [
    "KGExtractionError",
    "extract_entities_and_relations",
]


# Re-export for tests that want to introspect the prompt.
def system_prompt() -> str:  # pragma: no cover — debug helper
    """Return the system prompt used by the extractor."""
    return _SYSTEM_PROMPT


def _expose_test_internals() -> dict[str, Any]:  # pragma: no cover
    return {"strip_code_fence": _strip_code_fence, "parse_response": _parse_response}
