# src/tracking/session_tracker.py — v1
"""Per-document session tracking.

Consolidates all LLM call records for a single document execution
into a SessionStats object. See spec §20.4.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ayextractor.tracking.agent_tracker import aggregate_by_agent
from ayextractor.tracking.models import LLMCallRecord, SessionStats


def build_session_stats(
    document_id: str,
    records: list[LLMCallRecord],
    document_size_chars: int = 0,
    document_size_tokens_est: int = 0,
    budget_per_agent: int | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> SessionStats:
    """Build consolidated session stats from call records.

    Args:
        document_id: Document being processed.
        records: All LLM call records for this execution.
        document_size_chars: Source document character count.
        document_size_tokens_est: Estimated source token count.
        budget_per_agent: Optional per-agent token budget.
        start_time: Session start. Auto-detected from records if None.
        end_time: Session end. Auto-detected from records if None.

    Returns:
        SessionStats with full aggregation.
    """
    if not records:
        now = datetime.now(timezone.utc)
        return SessionStats(
            document_id=document_id,
            session_id=str(uuid.uuid4()),
            start_time=start_time or now,
            end_time=end_time or now,
            duration_seconds=0.0,
            document_size_chars=document_size_chars,
            document_size_tokens_est=document_size_tokens_est,
            total_llm_calls=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            estimated_cost_usd=0.0,
            cost_per_1k_chars=0.0,
            agents={},
            budget_total_allocated=0,
            budget_total_consumed=0,
            budget_usage_pct=0.0,
            steps_degraded=[],
            steps_failed=[],
        )

    agents = aggregate_by_agent(records, budget_per_agent)

    total_input = sum(a.total_input_tokens for a in agents.values())
    total_output = sum(a.total_output_tokens for a in agents.values())
    total_tokens = sum(a.total_tokens for a in agents.values())
    total_cache_read = sum(a.total_cache_read_tokens for a in agents.values())
    total_cache_write = sum(a.total_cache_write_tokens for a in agents.values())
    total_cost = sum(a.estimated_cost_usd for a in agents.values())
    total_calls = sum(a.total_calls for a in agents.values())

    # Time boundaries
    timestamps = [r.timestamp for r in records]
    s_time = start_time or min(timestamps)
    e_time = end_time or max(timestamps)
    duration = (e_time - s_time).total_seconds()

    # Cost per 1k chars
    cost_per_1k = (total_cost / (document_size_chars / 1000.0)) if document_size_chars > 0 else 0.0

    # Budget tracking
    num_agents = len(agents)
    budget_allocated = (budget_per_agent * num_agents) if budget_per_agent else 0
    budget_pct = (total_tokens / budget_allocated * 100.0) if budget_allocated > 0 else 0.0

    steps_degraded = [
        name for name, a in agents.items()
        if budget_per_agent and a.total_tokens > budget_per_agent
    ]
    steps_failed = [name for name, a in agents.items() if a.failure_count > 0]

    return SessionStats(
        document_id=document_id,
        session_id=str(uuid.uuid4()),
        start_time=s_time,
        end_time=e_time,
        duration_seconds=duration,
        document_size_chars=document_size_chars,
        document_size_tokens_est=document_size_tokens_est,
        total_llm_calls=total_calls,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_tokens,
        total_cache_read_tokens=total_cache_read,
        total_cache_write_tokens=total_cache_write,
        estimated_cost_usd=total_cost,
        cost_per_1k_chars=cost_per_1k,
        agents=agents,
        budget_total_allocated=budget_allocated,
        budget_total_consumed=total_tokens,
        budget_usage_pct=budget_pct,
        steps_degraded=steps_degraded,
        steps_failed=steps_failed,
    )
