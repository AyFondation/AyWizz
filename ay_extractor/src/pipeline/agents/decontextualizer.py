# src/pipeline/agents/decontextualizer.py — v1
"""Chunk decontextualization agent — coreference resolution.

Resolves ambiguous references (pronouns, definite articles, acronyms,
implicit references) in each chunk using context from preceding chunks,
cumulative summary, document title, and table of contents.

Runs in phase 2c-i, interleaved with the Summarizer.
See spec §28 for full documentation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ayextractor.core.models import (
    Chunk,
    ChunkDecontextualization,
    DocumentStructure,
    Reference,
    ResolvedReference,
)
from ayextractor.llm.models import Message
from ayextractor.pipeline.plugin_kit.base_agent import BaseAgent
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput

if TYPE_CHECKING:
    from ayextractor.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "decontextualizer.txt"


class DecontextualizerInput(BaseModel):
    """Input schema for decontextualizer."""

    chunk: Chunk
    refine_summary: str
    preceding_chunks: list[Chunk]
    document_title: str
    language: str
    structure: DocumentStructure | None = None
    references: list[Reference] | None = None


class DecontextualizerOutput(BaseModel):
    """Output schema for decontextualizer."""

    decontextualized_content: str
    resolved_references: list[ResolvedReference]
    confidence: float


class DecontextualizerAgent(BaseAgent):
    """Resolve ambiguous references in chunks via LLM.

    Produces a self-contained version of each chunk where all pronouns,
    acronyms, and implicit references are resolved inline.
    """

    DEFAULT_WINDOW_SIZE = 3

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE):
        self._window_size = window_size
        self._prompt_template: str | None = None

    @property
    def name(self) -> str:
        return "decontextualizer"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Resolve ambiguous references (pronouns, acronyms, definite articles) in chunks"

    @property
    def input_schema(self) -> type[BaseModel]:
        return DecontextualizerInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return DecontextualizerOutput

    @property
    def prompt_file(self) -> str | None:
        return str(_PROMPT_PATH)

    def _load_prompt(self) -> str:
        """Load and cache prompt template."""
        if self._prompt_template is None:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template

    def _build_toc(self, structure: DocumentStructure | None) -> str:
        """Build table of contents string from document structure."""
        if structure is None or not structure.sections:
            return "(not available)"
        lines = []
        for section in structure.sections:
            indent = "  " * (section.level - 1)
            lines.append(f"{indent}- {section.title}")
        return "\n".join(lines)

    def _build_preceding_text(self, preceding_chunks: list[Chunk]) -> str:
        """Format preceding chunks for context window."""
        if not preceding_chunks:
            return "(no preceding chunks)"
        window = preceding_chunks[-self._window_size:]
        parts = []
        for c in window:
            parts.append(f"[{c.id}]:\n{c.content}")
        return "\n\n---\n\n".join(parts)

    def _format_prompt(self, inp: DecontextualizerInput) -> str:
        """Fill prompt template with input data."""
        template = self._load_prompt()
        return template.format(
            document_title=inp.document_title,
            language=inp.language,
            toc=self._build_toc(inp.structure),
            refine_summary=inp.refine_summary or "(no summary yet — this is the first chunk)",
            preceding_chunks=self._build_preceding_text(inp.preceding_chunks),
            chunk_content=inp.chunk.content,
        )

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse LLM JSON response, handling markdown fences."""
        text = content.strip()
        if text.startswith("```"):
            # Strip markdown code fences
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)

    async def execute(self, state: object, llm: BaseLLMClient) -> AgentOutput:
        """Execute decontextualization on a single chunk.

        Note: Unlike DAG agents, this is called per-chunk by the orchestrator
        during the interleaved loop (phase 2c). The 'state' is expected to be
        a DecontextualizerInput instance or a dict with the same fields.
        """
        if isinstance(state, dict):
            inp = DecontextualizerInput(**state)
        elif isinstance(state, DecontextualizerInput):
            inp = state
        else:
            raise TypeError(f"Expected DecontextualizerInput or dict, got {type(state)}")

        start_ms = time.monotonic_ns() // 1_000_000
        prompt = self._format_prompt(inp)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        response = await llm.complete(
            messages=[Message(role="user", content=prompt)],
            system="You are a coreference resolution specialist. Respond only with valid JSON.",
            temperature=0.1,
        )

        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms

        try:
            parsed = self._parse_response(response.content)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Decontextualizer JSON parse failed for %s: %s", inp.chunk.id, exc)
            # Fallback: return original content unchanged
            parsed = {
                "decontextualized_content": inp.chunk.content,
                "resolved_references": [],
                "confidence": 0.0,
            }

        # Build resolved references
        resolved = []
        for ref in parsed.get("resolved_references", []):
            try:
                resolved.append(ResolvedReference(
                    original_text=ref["original_text"],
                    resolved_text=ref["resolved_text"],
                    reference_type=ref.get("reference_type", "implicit_ref"),
                    resolution_source=ref.get("resolution_source", "preceding_chunk"),
                    position_in_chunk=ref.get("position_in_chunk", 0),
                ))
            except (KeyError, ValueError) as exc:
                logger.debug("Skipping malformed resolved reference: %s", exc)

        confidence = float(parsed.get("confidence", 0.5))

        return AgentOutput(
            data={
                "decontextualized_content": parsed.get(
                    "decontextualized_content", inp.chunk.content
                ),
                "resolved_references": [r.model_dump() for r in resolved],
                "confidence": confidence,
                "chunk_id": inp.chunk.id,
            },
            confidence=confidence,
            metadata=AgentMetadata(
                agent_name=self.name,
                agent_version=self.version,
                execution_time_ms=elapsed_ms,
                llm_calls=1,
                tokens_used=response.input_tokens + response.output_tokens,
                prompt_hash=prompt_hash,
            ),
        )

    def apply_to_chunk(self, chunk: Chunk, output: AgentOutput) -> Chunk:
        """Apply decontextualization result to a chunk (mutates in place).

        Args:
            chunk: The chunk to update.
            output: The agent output from execute().

        Returns:
            The updated chunk with decontextualized content.
        """
        data = output.data
        resolved = [
            ResolvedReference(**r) for r in data.get("resolved_references", [])
        ]

        # Preserve original content before overwriting
        chunk.original_content = chunk.content
        chunk.content = data["decontextualized_content"]
        chunk.decontextualization = ChunkDecontextualization(
            applied=True,
            resolved_references=resolved,
            context_window_size=self._window_size,
            confidence=data.get("confidence", output.confidence),
        )
        return chunk

    def validate_output(self, output: AgentOutput) -> float:
        """Validate decontextualizer output quality."""
        data = output.data
        content = data.get("decontextualized_content", "")
        if not content.strip():
            return 0.0
        return output.confidence
