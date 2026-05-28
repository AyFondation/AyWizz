# =============================================================================
# File: contextualizer.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c7_memory/contextualizer.py
# Description: Cumulative chunk contextualisation for ingestion (V2 #3-A.c /
#              R-400-203, the Anthropic "Contextual Retrieval" pattern).
#
#              For each chunk, a small configurable model (Haiku-class via C8,
#              agent route `c7-contextualizer`) is asked for a short context
#              that situates the chunk in its source document and resolves
#              anaphora. The DOCUMENT is sent as a STABLE system-prompt prefix
#              on every per-chunk call, so a prompt-caching provider amortises
#              it (R-400-203 "document prefix supplied via prompt caching") —
#              the per-chunk marginal cost is just the chunk + the short
#              completion.
#
#              The contextualised text (context + chunk) is what gets EMBEDDED
#              (R-400-203) ; the raw chunk is still stored as `content` (for
#              display + the BM25 lexical arm of R-400-202).
#
#              BEST-EFFORT : any per-chunk failure yields an empty context for
#              that chunk (the raw chunk is embedded) — contextualisation
#              SHALL NOT fail ingestion. Returns one string per input chunk.
# =============================================================================

from __future__ import annotations

from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)

# Document prefix budget. Kept stable across a source's chunks so the provider
# can cache it ; large enough for most specs/modules, bounded for cost.
_MAX_DOC_CHARS = 8000
# Hard cap on a chunk fed to the contextualiser (the chunk is short anyway).
_MAX_CHUNK_CHARS = 4000

_SYSTEM_PREFIX = (
    "You situate a text chunk within its source document for retrieval. "
    "Given the WHOLE DOCUMENT (below) and one CHUNK from it, reply with a "
    "SINGLE short sentence (<= 25 words) that states what the chunk is about "
    "in the document's context and resolves pronouns/anaphora. Output ONLY "
    "that sentence — no preamble, no quotes.\n\nDOCUMENT:\n"
)


def _assistant_text(response: object) -> str:
    """Extract the assistant text from a chat-completion response, tolerant
    of the OpenAI multi-modal (list-of-parts) content shape."""
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    content = getattr(choices[0].message, "content", None)
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return content.strip() if isinstance(content, str) else ""


async def contextualise_chunks(
    *,
    llm_client: LLMGatewayClient,
    document: str,
    chunk_texts: list[str],
    agent_name: str,
    tenant_id: str,
    project_id: str,
    source_id: str,
) -> list[str]:
    """Return one context sentence per chunk (`""` when generation fails or
    the document is empty). The document is the stable cached prefix ; each
    chunk is one call. Best-effort — never raises."""
    doc = document.strip()
    if not doc or not chunk_texts:
        return ["" for _ in chunk_texts]
    if len(doc) > _MAX_DOC_CHARS:
        doc = doc[:_MAX_DOC_CHARS] + "…"
    system_prefix = _SYSTEM_PREFIX + doc

    contexts: list[str] = []
    for chunk in chunk_texts:
        snippet = chunk[:_MAX_CHUNK_CHARS]
        try:
            response = await llm_client.chat_completion(
                ChatCompletionRequest(
                    messages=[
                        ChatMessage(role=ChatRole.SYSTEM, content=system_prefix),
                        ChatMessage(
                            role=ChatRole.USER,
                            content=f"CHUNK:\n{snippet}\n\nContext sentence:",
                        ),
                    ],
                    stream=False,
                ),
                agent_name=agent_name,
                session_id=f"ctx:{source_id}",
                tenant_id=tenant_id,
                project_id=project_id,
            )
            contexts.append(_assistant_text(response))
        except Exception:
            # Best-effort : a failed context just means the raw chunk is
            # embedded for that one. Never break ingestion.
            contexts.append("")
    return contexts
