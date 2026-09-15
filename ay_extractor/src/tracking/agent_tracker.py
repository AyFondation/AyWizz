# src/tracking/agent_tracker.py — v1
"""Per-agent aggregation of LLM call records.

Aggregates LLMCallRecord entries by agent name to produce AgentStats.
See spec §20.3.
"""

from __future__ import annotations

from collections import defaultdict

from ayextractor.tracking.models import AgentStats, LLMCallRecord


def aggregate_by_agent(
    records: list[LLMCallRecord],
    budget_per_agent: int | None = None,
) -> dict[str, AgentStats]:
    """Aggregate call records into per-agent statistics.

    Args:
        records: Raw LLM call records from call_logger.
        budget_per_agent: Optional token budget per agent for usage % calc.

    Returns:
        Dict mapping agent name to AgentStats.
    """
    grouped: dict[str, list[LLMCallRecord]] = defaultdict(list)
    for rec in records:
        grouped[rec.agent].append(rec)

    result: dict[str, AgentStats] = {}
    for agent_name, agent_records in grouped.items():
        total_calls = len(agent_records)
        total_input = sum(r.input_tokens for r in agent_records)
        total_output = sum(r.output_tokens for r in agent_records)
        total_tokens = sum(r.total_tokens for r in agent_records)
        total_cache_read = sum(r.cache_read_tokens for r in agent_records)
        total_cache_write = sum(r.cache_write_tokens for r in agent_records)
        latencies = [r.latency_ms for r in agent_records]
        avg_latency = sum(latencies) / total_calls if total_calls else 0.0
        max_latency = max(latencies) if latencies else 0
        retry_count = sum(r.retry_count for r in agent_records)
        failure_count = sum(1 for r in agent_records if r.status == "failed")
        estimated_cost = sum(r.estimated_cost_usd for r in agent_records)

        budget_pct = 0.0
        if budget_per_agent and budget_per_agent > 0:
            budget_pct = (total_tokens / budget_per_agent) * 100.0

        result[agent_name] = AgentStats(
            agent=agent_name,
            total_calls=total_calls,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_tokens,
            total_cache_read_tokens=total_cache_read,
            total_cache_write_tokens=total_cache_write,
            avg_latency_ms=avg_latency,
            max_latency_ms=max_latency,
            retry_count=retry_count,
            failure_count=failure_count,
            estimated_cost_usd=estimated_cost,
            budget_usage_pct=budget_pct,
        )
    return result
