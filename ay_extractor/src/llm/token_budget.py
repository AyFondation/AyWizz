# src/llm/token_budget.py — v2
"""Token budget estimation and allocation per agent.

D-020 v1 strip: Phase 3 agents removed from the budget table
(concept_extractor, entity_normalizer, relation_normalizer,
community_summarizer, profile_generator, synthesizer, critic) +
reference_extractor (now library-based, no LLM). Added
decontextualizer_screener per D-020 v2 §A.

See spec §15.1 for original estimation formula; D-020 R-100-125 for the
v1 scope.
"""

from __future__ import annotations

import logging

from ayextractor.config.settings import Settings
from ayextractor.core.models import TokenBudget

logger = logging.getLogger(__name__)

# Rough cost multipliers per agent (tokens per input token) — D-020 v1 scope.
_AGENT_COST_RATIOS: dict[str, float] = {
    "image_analyzer": 0.15,
    "decontextualizer_screener": 0.005,  # ~50 in, ~10 out per chunk
    "decontextualizer": 0.25,
    "summarizer": 0.20,
    "densifier": 0.05,
}


def estimate_budget(
    text_tokens: int,
    n_chunks: int,
    n_images: int = 0,
    settings: Settings | None = None,
) -> TokenBudget:
    """Estimate total token budget for a document analysis (D-020 v1 scope).

    Args:
        text_tokens: Estimated tokens in the full extracted text.
        n_chunks: Number of chunks after chunking.
        n_images: Number of UNIQUE embedded images (post-dedup, R-100-125 §5).
        settings: Application settings (for density_iterations etc.).

    Returns:
        TokenBudget with per-agent allocations covering Phase 1+2 only.
    """
    density_iter = 5 if settings is None else settings.density_iterations
    max_per_agent = 4096 if settings is None else settings.llm_max_tokens_per_agent

    per_agent: dict[str, int] = {}

    # Image analyzer: ~1000 tokens per UNIQUE image (vision)
    per_agent["image_analyzer"] = n_images * 1000

    # Screener: ~60 tokens per chunk (50 in + 10 out)
    per_agent["decontextualizer_screener"] = n_chunks * 60

    # Decontextualizer: 1 call per chunk that the screener said YES on.
    # Conservatively budget for ALL chunks; actual spend will be lower.
    per_agent["decontextualizer"] = n_chunks * max_per_agent

    # Summarizer: 1 call per chunk (Refine)
    per_agent["summarizer"] = n_chunks * max_per_agent

    # Densifier: density_iterations passes on summary
    per_agent["densifier"] = density_iter * max_per_agent

    total = sum(per_agent.values())

    return TokenBudget(
        total_estimated=total,
        per_agent=per_agent,
    )


def check_budget(budget: TokenBudget, agent: str) -> bool:
    """Check if an agent has remaining budget.

    Returns True if within budget, False if over.
    """
    allocated = budget.per_agent.get(agent, 0)
    consumed = budget.consumed.get(agent, 0)
    if consumed > allocated:
        logger.warning(
            "Agent '%s' over budget: %d/%d tokens consumed",
            agent, consumed, allocated,
        )
        return False
    return True


def record_usage(budget: TokenBudget, agent: str, tokens: int) -> None:
    """Record token usage for an agent (mutates budget in place)."""
    budget.consumed[agent] = budget.consumed.get(agent, 0) + tokens
