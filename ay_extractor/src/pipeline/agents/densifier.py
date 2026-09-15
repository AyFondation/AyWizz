# src/pipeline/agents/densifier.py — v1
"""Chain of Density densifier agent.

Takes the final refine summary and iteratively condenses it through
multiple passes, each making the summary denser while preserving
critical information. Produces the global_summary injected into all chunks.

Runs in phase 2d, after the interleaved decontextualizer/summarizer loop.
See spec §6.1 (Densifier row) and pipeline step 2d.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ayextractor.llm.models import Message
from ayextractor.pipeline.plugin_kit.base_agent import BaseAgent
from ayextractor.pipeline.plugin_kit.models import AgentMetadata, AgentOutput

if TYPE_CHECKING:
    from ayextractor.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "densifier.txt"

DEFAULT_NUM_ITERATIONS = 5


class DensifierInput(BaseModel):
    """Input schema for densifier."""

    refine_summary: str
    document_title: str
    language: str
    num_iterations: int = DEFAULT_NUM_ITERATIONS


class DensifierOutput(BaseModel):
    """Output schema for densifier."""

    dense_summary: str
    iterations: list[dict[str, Any]]
    final_density_score: float


class DensifierAgent(BaseAgent):
    """Chain of Density summarizer.

    Iteratively condenses the refine summary through N passes.
    Each pass maintains roughly the same length but packs more information.
    The final iteration becomes the document's global_summary.
    """

    def __init__(self, num_iterations: int = DEFAULT_NUM_ITERATIONS):
        self._num_iterations = num_iterations
        self._prompt_template: str | None = None

    @property
    def name(self) -> str:
        return "densifier"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Chain of Density — iteratively condense refine summary into dense global summary"

    @property
    def input_schema(self) -> type[BaseModel]:
        return DensifierInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return DensifierOutput

    @property
    def dependencies(self) -> list[str]:
        return ["summarizer"]

    @property
    def prompt_file(self) -> str | None:
        return str(_PROMPT_PATH)

    def _load_prompt(self) -> str:
        """Load and cache prompt template."""
        if self._prompt_template is None:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template

    def _format_prompt(
        self,
        inp: DensifierInput,
        current_iteration: int,
        previous_iterations: list[dict[str, Any]],
    ) -> str:
        """Fill prompt template for a specific iteration."""
        template = self._load_prompt()

        prev_text = "(none — this is the first iteration)"
        if previous_iterations:
            parts = []
            for i, it in enumerate(previous_iterations, 1):
                parts.append(f"Iteration {i}: {it.get('dense_summary', '')[:200]}...")
            prev_text = "\n".join(parts)

        return template.format(
            document_title=inp.document_title,
            language=inp.language,
            refine_summary=inp.refine_summary,
            num_iterations=inp.num_iterations,
            current_iteration=current_iteration,
            previous_iterations=prev_text,
        )

    def _parse_response(self, content: str) -> dict[str, Any]:
        """Parse LLM JSON response, handling markdown fences."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)

    async def execute(self, state: object, llm: BaseLLMClient) -> AgentOutput:
        """Execute Chain of Density through N iterations.

        Unlike per-chunk agents, this runs once after all chunks are processed.
        """
        if isinstance(state, dict):
            inp = DensifierInput(**state)
        elif isinstance(state, DensifierInput):
            inp = state
        else:
            raise TypeError(f"Expected DensifierInput or dict, got {type(state)}")

        num_iterations = inp.num_iterations or self._num_iterations
        start_ms = time.monotonic_ns() // 1_000_000
        total_tokens = 0
        iterations: list[dict[str, Any]] = []
        current_summary = inp.refine_summary
        last_prompt_hash = ""

        for i in range(1, num_iterations + 1):
            prompt = self._format_prompt(inp, i, iterations)
            last_prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

            response = await llm.complete(
                messages=[Message(role="user", content=prompt)],
                system="You are a document summarization expert. Respond only with valid JSON.",
                temperature=0.2,
            )
            total_tokens += response.input_tokens + response.output_tokens

            try:
                parsed = self._parse_response(response.content)
                iteration_result = {
                    "iteration": i,
                    "dense_summary": parsed.get("dense_summary", current_summary),
                    "added_entities": parsed.get("added_entities", []),
                    "removed_details": parsed.get("removed_details", []),
                    "density_score": float(parsed.get("density_score", 0.5)),
                }
                current_summary = iteration_result["dense_summary"]
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Densifier iteration %d parse failed: %s", i, exc)
                iteration_result = {
                    "iteration": i,
                    "dense_summary": current_summary,
                    "added_entities": [],
                    "removed_details": [],
                    "density_score": 0.3,
                    "parse_error": str(exc),
                }

            iterations.append(iteration_result)

        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        final_density = iterations[-1].get("density_score", 0.5) if iterations else 0.0

        return AgentOutput(
            data={
                "dense_summary": current_summary,
                "iterations": iterations,
                "final_density_score": final_density,
            },
            confidence=final_density,
            metadata=AgentMetadata(
                agent_name=self.name,
                agent_version=self.version,
                execution_time_ms=elapsed_ms,
                llm_calls=num_iterations,
                tokens_used=total_tokens,
                prompt_hash=last_prompt_hash,
            ),
        )

    def validate_output(self, output: AgentOutput) -> float:
        """Validate densifier output quality."""
        summary = output.data.get("dense_summary", "")
        if not summary.strip():
            return 0.0
        iterations = output.data.get("iterations", [])
        if not iterations:
            return 0.0
        # Check density improved across iterations
        scores = [it.get("density_score", 0) for it in iterations]
        if len(scores) >= 2 and scores[-1] > scores[0]:
            return min(output.confidence + 0.1, 1.0)
        return output.confidence
