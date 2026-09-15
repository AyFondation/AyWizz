# src/tracking/cost_calculator.py — v1
"""Cost calculation from LLM call records.

Computes estimated USD cost per agent, per model, and total.
See spec §20.
"""

from __future__ import annotations

from collections import defaultdict

from ayextractor.tracking.models import (
    AgentStats,
    LLMCallRecord,
    ModelPricing,
    SessionStats,
)

# Default pricing per 1M tokens (as of spec writing)
DEFAULT_PRICING: dict[str, ModelPricing] = {
    "claude-sonnet-4-20250514": ModelPricing(
        model="claude-sonnet-4-20250514",
        input_price_per_1m=3.0, output_price_per_1m=15.0,
        cache_read_per_1m=0.3, cache_write_per_1m=3.75,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        model="claude-haiku-4-5-20251001",
        input_price_per_1m=0.80, output_price_per_1m=4.0,
        cache_read_per_1m=0.08, cache_write_per_1m=1.0,
    ),
    "gpt-4o": ModelPricing(
        model="gpt-4o",
        input_price_per_1m=2.50, output_price_per_1m=10.0,
    ),
    "gpt-4o-mini": ModelPricing(
        model="gpt-4o-mini",
        input_price_per_1m=0.15, output_price_per_1m=0.60,
    ),
    "gemini-pro": ModelPricing(
        model="gemini-pro",
        input_price_per_1m=1.25, output_price_per_1m=5.0,
    ),
}


def compute_call_cost(record: LLMCallRecord, pricing: dict[str, ModelPricing] | None = None) -> float:
    """Compute estimated cost for a single LLM call in USD."""
    pricing = pricing or DEFAULT_PRICING
    p = pricing.get(record.model)
    if p is None:
        return 0.0

    cost = (record.input_tokens * p.input_price_per_1m / 1_000_000
            + record.output_tokens * p.output_price_per_1m / 1_000_000
            + record.cache_read_tokens * p.cache_read_per_1m / 1_000_000
            + record.cache_write_tokens * p.cache_write_per_1m / 1_000_000)
    return cost


def compute_agent_stats(
    records: list[LLMCallRecord],
    pricing: dict[str, ModelPricing] | None = None,
) -> dict[str, AgentStats]:
    """Compute per-agent statistics from call records."""
    pricing = pricing or DEFAULT_PRICING
    by_agent: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for r in records:
        by_agent[r.agent].append(r)

    result: dict[str, AgentStats] = {}
    for agent, agent_records in by_agent.items():
        total_input = sum(r.input_tokens for r in agent_records)
        total_output = sum(r.output_tokens for r in agent_records)
        total_tokens = sum(r.total_tokens for r in agent_records)
        latencies = [r.latency_ms for r in agent_records]
        total_cost = sum(compute_call_cost(r, pricing) for r in agent_records)

        result[agent] = AgentStats(
            agent=agent,
            total_calls=len(agent_records),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_tokens,
            avg_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
            max_latency_ms=max(latencies) if latencies else 0,
            estimated_cost_usd=total_cost,
        )

    return result


def compute_total_cost(
    records: list[LLMCallRecord],
    pricing: dict[str, ModelPricing] | None = None,
) -> float:
    """Compute total estimated cost across all records."""
    return sum(compute_call_cost(r, pricing) for r in records)
