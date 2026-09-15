# src/tracking/exporter.py — v1
"""Tracking data export to JSON, CSV, and summary text.

See spec §20.7.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

from ayextractor.tracking.models import (
    GlobalStats,
    LLMCallRecord,
    SessionStats,
)

logger = logging.getLogger(__name__)


def export_session_json(session: SessionStats, path: Path) -> None:
    """Export session stats as formatted JSON.

    Args:
        session: Session stats to export.
        path: Output file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.model_dump_json(indent=2), encoding="utf-8")


def export_session_csv(records: list[LLMCallRecord], path: Path) -> None:
    """Export raw call records as CSV for spreadsheet/BI analysis.

    Args:
        records: LLM call records.
        path: Output file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "call_id", "timestamp", "agent", "step", "provider", "model",
        "input_tokens", "output_tokens", "total_tokens",
        "cache_read_tokens", "cache_write_tokens",
        "latency_ms", "status", "retry_count", "estimated_cost_usd",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            row = record.model_dump()
            row["timestamp"] = str(row["timestamp"])
            writer.writerow(row)


def export_session_summary(session: SessionStats) -> str:
    """Generate a human-readable summary text of session stats.

    Args:
        session: Session stats.

    Returns:
        Formatted summary string.
    """
    lines: list[str] = [
        f"=== Execution Summary: {session.document_id} ===",
        f"Session ID : {session.session_id}",
        f"Duration   : {session.duration_seconds:.1f}s",
        f"LLM Calls  : {session.total_llm_calls}",
        f"Tokens     : {session.total_tokens:,} "
        f"(in: {session.total_input_tokens:,}, out: {session.total_output_tokens:,})",
        f"Cache      : read {session.total_cache_read_tokens:,}, "
        f"write {session.total_cache_write_tokens:,}",
        f"Est. Cost  : ${session.estimated_cost_usd:.4f}",
        f"Cost/1k ch : ${session.cost_per_1k_chars:.6f}",
        f"Budget     : {session.budget_usage_pct:.1f}% "
        f"({session.budget_total_consumed:,}/{session.budget_total_allocated:,})",
        "",
        "--- Per Agent ---",
    ]

    for name, agent in sorted(session.agents.items()):
        lines.append(
            f"  {name:25s} | {agent.total_calls:4d} calls | "
            f"{agent.total_tokens:8,} tokens | "
            f"${agent.estimated_cost_usd:.4f} | "
            f"avg {agent.avg_latency_ms:.0f}ms"
        )

    if session.steps_degraded:
        lines.append(f"\n⚠ Degraded steps: {', '.join(session.steps_degraded)}")
    if session.steps_failed:
        lines.append(f"✗ Failed steps: {', '.join(session.steps_failed)}")

    return "\n".join(lines)


def export_global_json(stats: GlobalStats, path: Path) -> None:
    """Export global cumulative stats as JSON.

    Args:
        stats: Global stats.
        path: Output file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stats.model_dump_json(indent=2), encoding="utf-8")


def export_global_summary(stats: GlobalStats) -> str:
    """Generate a human-readable summary of global stats.

    Args:
        stats: Global cumulative stats.

    Returns:
        Formatted summary string.
    """
    lines: list[str] = [
        "=== Global Cumulative Stats ===",
        f"Documents processed : {stats.total_documents_processed}",
        f"Total tokens        : {stats.total_tokens_consumed:,}",
        f"Total est. cost     : ${stats.total_estimated_cost_usd:.4f}",
        f"Avg tokens/doc      : {stats.avg_tokens_per_document:,.0f}",
        f"Avg cost/doc        : ${stats.avg_cost_per_document:.4f}",
        f"Avg duration/doc    : {stats.avg_duration_per_document:.1f}s",
    ]

    if stats.by_document_type:
        lines.append("\n--- By Document Type ---")
        for dtype, ts in sorted(stats.by_document_type.items()):
            lines.append(
                f"  {dtype:10s} | {ts.count:4d} docs | "
                f"avg {ts.avg_tokens:.0f} tokens | "
                f"avg ${ts.avg_cost_usd:.4f}"
            )

    if stats.by_model:
        lines.append("\n--- By Model ---")
        for model, ms in sorted(stats.by_model.items()):
            lines.append(
                f"  {model:35s} | {ms.total_calls:5d} calls | "
                f"${ms.total_cost_usd:.4f}"
            )

    return "\n".join(lines)