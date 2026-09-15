# src/pipeline/enrichment.py — v1
"""Phase-2 text-enrichment orchestrator (config-driven).

The per-chunk enrichment agents (decontextualizer, summarizer) and the
document-level densifier expect their OWN Input objects, not a global pipeline
state — a generic state-passing DAG runner does not fit them (they would reject
the whole-state call with a `TypeError`). This module drives them directly, in
the interleaved order the spec describes (decontextualize → refine-summarize
per chunk, then densify once), honouring a resolved per-option configuration.

Resolution: a `quality_tier` preset provides defaults; any explicit
`ConfigOverrides` boolean overrides its preset value. This is the seam the
per-project enrichment configuration plugs into (it only has to fill a
`ConfigOverrides`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ayextractor.api.models import ConfigOverrides
    from ayextractor.pipeline.state import PipelineState

logger = logging.getLogger(__name__)

DEFAULT_DENSIFIER_ITERATIONS = 5

# (summarization, decontextualization, densification, image_vision) per tier.
_TIER_PRESETS: dict[str, tuple[bool, bool, bool, bool]] = {
    "minimal": (False, False, False, False),
    "standard": (True, False, False, True),
    "high": (True, True, True, True),
}


@dataclass(frozen=True)
class ResolvedEnrichment:
    """Effective per-option enrichment flags for a run."""

    summarization: bool
    decontextualization: bool
    densification: bool
    image_vision: bool
    densifier_iterations: int

    @property
    def any_text(self) -> bool:
        return self.summarization or self.decontextualization or self.densification

    @property
    def needs_llm(self) -> bool:
        return self.any_text or self.image_vision


def resolve_enrichment(
    quality_tier: str,
    overrides: ConfigOverrides | None,
    default_iterations: int = DEFAULT_DENSIFIER_ITERATIONS,
) -> ResolvedEnrichment:
    """Compute effective flags: tier preset, then explicit overrides win.

    Densification depends on the refine summary, so enabling it implies
    summarization (otherwise there is nothing to densify)."""
    summ, dec, den, vision = _TIER_PRESETS.get(quality_tier, (False, False, False, False))
    iters = default_iterations
    if overrides is not None:
        if overrides.summarization_enabled is not None:
            summ = overrides.summarization_enabled
        if overrides.decontextualization_enabled is not None:
            dec = overrides.decontextualization_enabled
        if overrides.densification_enabled is not None:
            den = overrides.densification_enabled
        if overrides.image_vision_enabled is not None:
            vision = overrides.image_vision_enabled
        if overrides.chain_of_density_iterations is not None:
            iters = overrides.chain_of_density_iterations
    if den and not summ:
        summ = True  # densifier needs a refine summary to condense
    return ResolvedEnrichment(
        summarization=summ,
        decontextualization=dec,
        densification=den,
        image_vision=vision,
        densifier_iterations=max(1, iters),
    )


def _account(state: PipelineState, name: str, out: Any) -> None:
    """Fold an agent output's cost into the run state (best-effort).

    `record_agent_output` already bumps `total_llm_calls` / `total_tokens_used`,
    so we must not increment them again here."""
    try:
        state.record_agent_output(name, out)
    except Exception:  # noqa: BLE001 — accounting must never break a run
        logger.debug("accounting failed for agent %s", name, exc_info=True)


# Bounded concurrency for the independent per-chunk/batch LLM calls — parallel
# (vs. the old sequential refine) without hammering the C8 gateway. MAP groups
# chunks into batches to cut the call COUNT; REDUCE fans them in hierarchically.
_MAP_CONCURRENCY = 8
_MAP_BATCH = 5
_REDUCE_FANIN = 10


async def _bounded_gather(coros: list[Any], limit: int) -> list[Any]:
    """Await `coros` with bounded concurrency. Exceptions are returned in place
    (never raised) so one failure cannot abort the batch."""
    sem = asyncio.Semaphore(limit)

    async def _run(coro: Any) -> Any:
        async with sem:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)


async def _reduce_summaries(
    sum_agent: Any,
    partials: list[str],
    *,
    document_title: str,
    language: str,
    llm_factory: Any,
    state: PipelineState,
) -> str:
    """Combine partial summaries into ONE document summary, hierarchically
    (fan-in `_REDUCE_FANIN` per call, batches combined concurrently) so it
    scales to large documents. Falls back to concatenation on a failed combine."""
    from ayextractor.pipeline.agents.summarizer import SummarizerInput

    summaries = [p for p in partials if p]
    if not summaries:
        return ""
    level = 0
    while len(summaries) > 1:
        groups = [
            summaries[i : i + _REDUCE_FANIN]
            for i in range(0, len(summaries), _REDUCE_FANIN)
        ]
        logger.info(
            "enrichment.reduce L%d: %d summaries -> %d", level, len(summaries), len(groups)
        )

        async def _combine(group: list[str]) -> str:
            if len(group) == 1:
                return group[0]
            llm = llm_factory("summarizer")
            out = await sum_agent.execute(
                SummarizerInput(
                    text="\n\n---\n\n".join(group),
                    document_title=document_title,
                    language=language,
                ),
                llm,
            )
            _account(state, "summarizer", out)
            return str(out.data.get("summary") or "\n\n".join(group))

        results = await _bounded_gather([_combine(g) for g in groups], _MAP_CONCURRENCY)
        next_level: list[str] = []
        for res, group in zip(results, groups, strict=True):
            if isinstance(res, Exception):
                state.errors.append(f"summarizer.reduce: {res}")
                next_level.append("\n\n".join(group))
            else:
                next_level.append(res)
        summaries = next_level
        level += 1
    return summaries[0]


async def run_text_enrichment(
    *,
    state: PipelineState,
    llm_factory: Any,
    document_title: str,
    resolved: ResolvedEnrichment,
) -> None:
    """Drive the text-enrichment agents over `state.chunks` (map-reduce).

    1. MAP   : summarise chunk batches CONCURRENTLY → a per-region partial
               summary (stored as each chunk's `context_summary`).
    2. REDUCE: hierarchically combine the partials → `state.refine_summary`.
    3. DECON : (high tier) decontextualise each chunk CONCURRENTLY, using the
               document summary as context.
    4. DENSE : (high tier) chain-of-density over the document summary.

    Best-effort throughout: a single unit's failure is recorded in
    `state.errors` and never aborts the batch.
    """
    from ayextractor.pipeline.agents.decontextualizer import (
        DecontextualizerAgent,
        DecontextualizerInput,
    )
    from ayextractor.pipeline.agents.densifier import DensifierAgent, DensifierInput
    from ayextractor.pipeline.agents.summarizer import SummarizerAgent, SummarizerInput

    language = state.language or "en"
    chunks = state.chunks

    # --- MAP + REDUCE: document summary ---
    if resolved.summarization and chunks:
        _t0, _c0, _k0 = time.monotonic(), state.total_llm_calls, state.total_tokens_used
        sum_agent = SummarizerAgent()
        batches = [chunks[i : i + _MAP_BATCH] for i in range(0, len(chunks), _MAP_BATCH)]
        logger.info(
            "enrichment.map: %d chunks in %d batches (concurrency=%d)",
            len(chunks), len(batches), _MAP_CONCURRENCY,
        )

        async def _summarize_batch(batch: list[Any]) -> str:
            llm = llm_factory("summarizer")
            out = await sum_agent.execute(
                SummarizerInput(
                    text="\n\n".join(c.content for c in batch),
                    document_title=document_title,
                    language=language,
                ),
                llm,
            )
            _account(state, "summarizer", out)
            return str(out.data.get("summary") or "")

        results = await _bounded_gather(
            [_summarize_batch(b) for b in batches], _MAP_CONCURRENCY
        )
        partials: list[str] = []
        for res, batch in zip(results, batches, strict=True):
            if isinstance(res, Exception):
                state.errors.append(f"summarizer:batch: {res}")
                continue
            partials.append(res)
            for chunk in batch:  # share the region summary across its chunks
                chunk.context_summary = res

        state.refine_summary = await _reduce_summaries(
            sum_agent, partials,
            document_title=document_title, language=language,
            llm_factory=llm_factory, state=state,
        )
        logger.info(
            "enrichment.reduce: document summary ready (%d chars)", len(state.refine_summary)
        )
        state.record_phase(
            "summarize",
            duration_ms=int((time.monotonic() - _t0) * 1000),
            llm_calls=state.total_llm_calls - _c0,
            tokens=state.total_tokens_used - _k0,
        )

    # --- DECONTEXTUALIZE (concurrent ; uses the document summary as context) ---
    if resolved.decontextualization and chunks:
        _t0, _c0, _k0 = time.monotonic(), state.total_llm_calls, state.total_tokens_used
        dec_agent = DecontextualizerAgent()
        logger.info(
            "enrichment.decontextualize: %d chunks (concurrency=%d)",
            len(chunks), _MAP_CONCURRENCY,
        )

        async def _decontextualize(i: int, chunk: Any) -> tuple[int, Any]:
            llm = llm_factory("decontextualizer")
            out = await dec_agent.execute(
                DecontextualizerInput(
                    chunk=chunk,
                    refine_summary=state.refine_summary,
                    preceding_chunks=chunks[:i],
                    document_title=document_title,
                    language=language,
                    structure=state.structure,
                    references=state.references or None,
                ),
                llm,
            )
            return i, out

        results = await _bounded_gather(
            [_decontextualize(i, c) for i, c in enumerate(chunks)], _MAP_CONCURRENCY
        )
        for res in results:
            if isinstance(res, Exception):
                state.errors.append(f"decontextualizer: {res}")
                continue
            i, out = res
            dec_agent.apply_to_chunk(chunks[i], out)
            _account(state, "decontextualizer", out)
        state.record_phase(
            "decontextualize",
            duration_ms=int((time.monotonic() - _t0) * 1000),
            llm_calls=state.total_llm_calls - _c0,
            tokens=state.total_tokens_used - _k0,
        )

    # --- DENSIFY: chain-of-density over the document summary ---
    if resolved.densification and state.refine_summary:
        _t0, _c0, _k0 = time.monotonic(), state.total_llm_calls, state.total_tokens_used
        try:
            llm = llm_factory("densifier")
            out = await DensifierAgent(num_iterations=resolved.densifier_iterations).execute(
                DensifierInput(
                    refine_summary=state.refine_summary,
                    document_title=document_title,
                    language=language,
                    num_iterations=resolved.densifier_iterations,
                ),
                llm,
            )
            state.dense_summary = out.data.get("dense_summary") or ""
            _account(state, "densifier", out)
            logger.info(
                "enrichment.densify: dense summary ready (%d chars)", len(state.dense_summary)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("densifier failed: %s", exc)
            state.errors.append(f"densifier: {exc}")
        state.record_phase(
            "densify",
            duration_ms=int((time.monotonic() - _t0) * 1000),
            llm_calls=state.total_llm_calls - _c0,
            tokens=state.total_tokens_used - _k0,
        )

    # The per-chunk document summary (R-400-222 `global_summary`) is the dense
    # Chain-of-Density output when present (high tier), ELSE the map-reduce
    # document summary (standard tier) — so a document-level summary is always
    # surfaced when summarisation ran, not only on `high`. C7 de-duplicates it
    # (stored ONCE on the source row), so this is a reference, not a copy.
    document_summary = state.dense_summary or state.refine_summary
    if document_summary:
        for chunk in chunks:
            chunk.global_summary = document_summary
