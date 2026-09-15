# src/pipeline/agents/summarizer.py — v2
"""Map-reduce document summarizer agent.

Stateless: summarizes whatever `text` it is given — a single chunk (MAP) or a
set of partial summaries concatenated (REDUCE). The orchestrator
(`pipeline/enrichment.py`) runs the MAP step concurrently across chunk batches,
then REDUCEs the partials into the document summary.
See spec §6.1 (Summarizer row).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ayextractor.llm.models import Message
from ayextractor.pipeline.plugin_kit.base_agent import BaseAgent
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput
from pydantic import BaseModel

if TYPE_CHECKING:
    from ayextractor.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "summarizer.txt"


class SummarizerInput(BaseModel):
    """Input schema for a map-reduce summarization step.

    `text` is either a single chunk's content (MAP) or several partial summaries
    concatenated (REDUCE) — the agent summarizes whatever text it is given."""

    text: str
    document_title: str
    language: str


class SummarizerOutput(BaseModel):
    """Output schema for summarizer."""

    summary: str
    confidence: float


class SummarizerAgent(BaseAgent):
    """Stateless map-reduce summarizer.

    Summarizes the input `text` — a single chunk (MAP) or several partial
    summaries joined by `---` (REDUCE). The orchestrator runs MAP concurrently
    over chunk batches, then REDUCEs the partials into the document summary.
    """

    def __init__(self):
        self._prompt_template: str | None = None

    @property
    def name(self) -> str:
        return "summarizer"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Map-reduce summarizer — summarises a chunk (map) or joined partials (reduce)"

    @property
    def input_schema(self) -> type[BaseModel]:
        return SummarizerInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return SummarizerOutput

    @property
    def prompt_file(self) -> str | None:
        return str(_PROMPT_PATH)

    def _load_prompt(self) -> str:
        """Load and cache prompt template."""
        if self._prompt_template is None:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template

    def _format_prompt(self, inp: SummarizerInput) -> str:
        """Fill prompt template with input data."""
        template = self._load_prompt()
        return template.format(
            document_title=inp.document_title,
            language=inp.language,
            text=inp.text,
        )

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse LLM JSON response, handling markdown fences."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)

    async def execute(self, state: object, llm: BaseLLMClient) -> AgentOutput:
        """Summarize the input `text` (a chunk in MAP, joined partials in REDUCE).
        Stateless — safe to run concurrently across chunks/batches.
        """
        if isinstance(state, dict):
            inp = SummarizerInput(**state)
        elif isinstance(state, SummarizerInput):
            inp = state
        else:
            raise TypeError(f"Expected SummarizerInput or dict, got {type(state)}")

        start_ms = time.monotonic_ns() // 1_000_000
        prompt = self._format_prompt(inp)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        response = await llm.complete(
            messages=[Message(role="user", content=prompt)],
            system="You are a document summarization expert. Respond only with valid JSON.",
            temperature=0.2,
        )

        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms

        try:
            parsed = self._parse_response(response.content)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Summarizer JSON parse failed: %s", exc)
            # Fallback: a truncated copy of the input keeps the run going.
            parsed = {"summary": inp.text[:500], "confidence": 0.3}

        confidence = float(parsed.get("confidence", 0.5))

        return AgentOutput(
            data={
                "summary": parsed.get("summary", inp.text[:500]),
                "confidence": confidence,
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

    def validate_output(self, output: AgentOutput) -> float:
        """Validate summarizer output."""
        summary = output.data.get("summary", "")
        if not summary.strip():
            return 0.0
        # Penalize very short summaries (< 50 chars) as likely incomplete
        if len(summary) < 50:
            return min(output.confidence, 0.5)
        return output.confidence
