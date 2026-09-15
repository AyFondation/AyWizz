# src/tracking/stats_aggregator.py — v1
"""Cross-document cumulative statistics aggregator.

Maintains GlobalStats across all processed documents.
Persisted in {cache_root}/global_stats.json.
See spec §20.5.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ayextractor.tracking.cost_calculator import compute_call_cost
from ayextractor.tracking.models import (
    CumulativeAgentStats,
    DailyStats,
    GlobalStats,
    LLMCallRecord,
    ModelStats,
    SessionStats,
    TypeStats,
)

logger = logging.getLogger(__name__)


def load_global_stats(path: Path) -> GlobalStats | None:
    """Load global stats from disk.

    Args:
        path: Path to global_stats.json.

    Returns:
        GlobalStats or None if file doesn't exist.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return GlobalStats(**data)
    except Exception as e:
        logger.warning("Failed to load global stats from %s: %s", path, e)
        return None


def save_global_stats(stats: GlobalStats, path: Path) -> None:
    """Persist global stats to disk.

    Args:
        stats: GlobalStats to save.
        path: Target file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stats.model_dump_json(indent=2), encoding="utf-8")


def update_global_stats(
    current: GlobalStats | None,
    session: SessionStats,
    document_type: str = "unknown",
    num_chunks: int = 0,
    records: list[LLMCallRecord] | None = None,
) -> GlobalStats:
    """Merge a new session into cumulative global stats.

    Args:
        current: Existing global stats (None for first document).
        session: Session stats for the newly processed document.
        document_type: Document format (pdf, epub, docx, etc.).
        num_chunks: Number of chunks produced.
        records: Raw call records for model-level aggregation.

    Returns:
        Updated GlobalStats.
    """
    now = datetime.now(timezone.utc)

    if current is None:
        current = GlobalStats(
            total_documents_processed=0,
            total_tokens_consumed=0,
            total_estimated_cost_usd=0.0,
            avg_tokens_per_document=0.0,
            avg_cost_per_document=0.0,
            avg_duration_per_document=0.0,
            by_document_type={},
            by_agent={},
            by_model={},
            cost_trend=[],
            last_updated=now,
        )

    # Update totals
    n = current.total_documents_processed + 1
    total_tokens = current.total_tokens_consumed + session.total_tokens
    total_cost = current.total_estimated_cost_usd + session.estimated_cost_usd
    total_duration = current.avg_duration_per_document * current.total_documents_processed + session.duration_seconds

    # Update document type stats
    by_type = dict(current.by_document_type)
    if document_type in by_type:
        ts = by_type[document_type]
        type_n = ts.count + 1
        by_type[document_type] = TypeStats(
            count=type_n,
            avg_tokens=_running_avg(ts.avg_tokens, ts.count, session.total_tokens),
            avg_cost_usd=_running_avg(ts.avg_cost_usd, ts.count, session.estimated_cost_usd),
            avg_duration_seconds=_running_avg(ts.avg_duration_seconds, ts.count, session.duration_seconds),
            avg_chunks=_running_avg(ts.avg_chunks, ts.count, num_chunks),
        )
    else:
        by_type[document_type] = TypeStats(
            count=1,
            avg_tokens=float(session.total_tokens),
            avg_cost_usd=session.estimated_cost_usd,
            avg_duration_seconds=session.duration_seconds,
            avg_chunks=float(num_chunks),
        )

    # Update agent stats
    by_agent = dict(current.by_agent)
    for agent_name, agent_stats in session.agents.items():
        if agent_name in by_agent:
            existing = by_agent[agent_name]
            total_calls = existing.total_calls + agent_stats.total_calls
            total_agent_tokens = existing.total_tokens + agent_stats.total_tokens
            failures = int(existing.failure_rate * existing.total_calls) + agent_stats.failure_count
            by_agent[agent_name] = CumulativeAgentStats(
                total_calls=total_calls,
                total_tokens=total_agent_tokens,
                avg_tokens_per_call=total_agent_tokens / total_calls if total_calls else 0,
                failure_rate=failures / total_calls if total_calls else 0,
                avg_latency_ms=_running_avg(
                    existing.avg_latency_ms, existing.total_calls,
                    agent_stats.avg_latency_ms, agent_stats.total_calls,
                ),
                pct_of_total_cost=0.0,  # Recalculated below
            )
        else:
            by_agent[agent_name] = CumulativeAgentStats(
                total_calls=agent_stats.total_calls,
                total_tokens=agent_stats.total_tokens,
                avg_tokens_per_call=(
                    agent_stats.total_tokens / agent_stats.total_calls
                    if agent_stats.total_calls else 0
                ),
                failure_rate=(
                    agent_stats.failure_count / agent_stats.total_calls
                    if agent_stats.total_calls else 0
                ),
                avg_latency_ms=agent_stats.avg_latency_ms,
                pct_of_total_cost=0.0,
            )

    # Recalculate cost percentages for agents
    if total_cost > 0:
        for name in by_agent:
            agent_cost = by_agent[name].total_tokens * (total_cost / total_tokens) if total_tokens > 0 else 0
            by_agent[name] = by_agent[name].model_copy(
                update={"pct_of_total_cost": (agent_cost / total_cost) * 100.0}
            )

    # Update model stats from raw records
    by_model = dict(current.by_model)
    if records:
        for r in records:
            call_cost = compute_call_cost(r)
            if r.model in by_model:
                m = by_model[r.model]
                by_model[r.model] = ModelStats(
                    total_calls=m.total_calls + 1,
                    total_input_tokens=m.total_input_tokens + r.input_tokens,
                    total_output_tokens=m.total_output_tokens + r.output_tokens,
                    total_cost_usd=m.total_cost_usd + call_cost,
                )
            else:
                by_model[r.model] = ModelStats(
                    total_calls=1,
                    total_input_tokens=r.input_tokens,
                    total_output_tokens=r.output_tokens,
                    total_cost_usd=call_cost,
                )

    # Update daily cost trend
    today_str = now.strftime("%Y-%m-%d")
    cost_trend = list(current.cost_trend)
    if cost_trend and cost_trend[-1].date == today_str:
        last = cost_trend[-1]
        cost_trend[-1] = DailyStats(
            date=today_str,
            documents_processed=last.documents_processed + 1,
            total_tokens=last.total_tokens + session.total_tokens,
            total_cost_usd=last.total_cost_usd + session.estimated_cost_usd,
        )
    else:
        cost_trend.append(DailyStats(
            date=today_str,
            documents_processed=1,
            total_tokens=session.total_tokens,
            total_cost_usd=session.estimated_cost_usd,
        ))

    return GlobalStats(
        total_documents_processed=n,
        total_tokens_consumed=total_tokens,
        total_estimated_cost_usd=total_cost,
        avg_tokens_per_document=total_tokens / n,
        avg_cost_per_document=total_cost / n,
        avg_duration_per_document=total_duration / n,
        by_document_type=by_type,
        by_agent=by_agent,
        by_model=by_model,
        cost_trend=cost_trend,
        last_updated=now,
    )


def _running_avg(
    old_avg: float, old_count: int, new_value: float, new_count: int = 1
) -> float:
    """Compute incremental running average."""
    total_count = old_count + new_count
    if total_count == 0:
        return 0.0
    return (old_avg * old_count + new_value * new_count) / total_count
