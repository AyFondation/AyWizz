# =============================================================================
# File: synthesis.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/_observability/synthesis.py
# Description: Pure functions that build a "workflow envelope" from a list
#              of `event=span_summary` records — the phase-3 synthesiser
#              for Q-100-014.
#
#              IMPORTANT: this module is intentionally storage-agnostic.
#              Inputs are typed `Span` objects + free-form `events`
#              dicts; the producer is responsible for collecting them
#              from wherever logs live (the local LogRingBuffer in the
#              test tier; Loki / Elasticsearch / etc. in production
#              K8s). The synthesis algorithm — group by trace_id, sort
#              by timestamp, reconstruct parent/child via
#              parent_span_id, derive verdict — is the SAME regardless
#              of source.
#
#              `span_from_dict` is the shared parse path for backends
#              that already deliver a Python dict (Elasticsearch
#              `_source`); `parse_span_summary` wraps it for backends
#              that deliver raw JSON lines (Docker logs, Loki).
#
# @relation implements:R-100-104
# @relation implements:R-100-105
# @relation implements:R-100-124
# =============================================================================

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class Span:
    """One span as emitted by `TraceContextMiddleware` (`event=span_summary`)."""

    trace_id: str
    span_id: str
    parent_span_id: str
    component: str
    method: str
    path: str
    status_code: int
    duration_ms: float
    sampled: bool
    # `started_at` is derived: the log line's `timestamp` minus the
    # duration. Approximate (clock skew, JSON formatter resolution) but
    # accurate enough to order spans within a trace.
    started_at: datetime

    @property
    def operation(self) -> str:
        return f"{self.method} {self.path}".strip()

    @property
    def ended_at(self) -> datetime:
        return self.started_at + timedelta(milliseconds=self.duration_ms)


def span_from_dict(obj: Any) -> Span | None:
    """Build a Span from an already-parsed dict, or None if invalid.

    Shared parse path: backends that deliver structured documents
    directly (Elasticsearch `_source`) call this; backends that
    deliver JSON lines (Docker / Loki) go through `parse_span_summary`
    which is a thin wrapper.
    """
    if not isinstance(obj, dict):
        return None
    if obj.get("event") != "span_summary":
        return None

    timestamp_raw = obj.get("timestamp", "")
    try:
        ended_at = datetime.fromisoformat(str(timestamp_raw))
    except (ValueError, TypeError):
        return None

    try:
        duration_ms = float(obj.get("duration_ms", 0.0))
    except (TypeError, ValueError):
        return None
    started_at = ended_at - timedelta(milliseconds=duration_ms)

    return Span(
        trace_id=str(obj.get("trace_id", "")),
        span_id=str(obj.get("span_id", "")),
        parent_span_id=str(obj.get("parent_span_id", "")),
        component=str(obj.get("component", "")),
        method=str(obj.get("method", "")),
        path=str(obj.get("path", "")),
        status_code=int(obj.get("status_code", 0)),
        duration_ms=duration_ms,
        sampled=bool(obj.get("sampled", False)),
        started_at=started_at,
    )


def parse_span_summary(json_line: str) -> Span | None:
    """Parse a JSON log line; return a Span if it's a span_summary record,
    else None.

    Lenient: missing optional fields default to safe values; malformed
    JSON returns None rather than raising — the caller iterates over
    raw log streams that may include non-summary lines.
    """
    try:
        obj = json.loads(json_line)
    except (json.JSONDecodeError, ValueError):
        return None
    return span_from_dict(obj)


def parse_lines(lines: Iterable[str]) -> list[Span]:
    """Best-effort parse a stream of log lines into a list of Spans.

    Lines that aren't valid JSON or aren't span_summary records are
    silently dropped — the caller passes the entire log stream and
    this function filters for the relevant subset.
    """
    return [s for s in (parse_span_summary(ln) for ln in lines) if s is not None]


def group_by_trace(spans: Iterable[Span]) -> dict[str, list[Span]]:
    out: dict[str, list[Span]] = {}
    for span in spans:
        if not span.trace_id:
            continue
        out.setdefault(span.trace_id, []).append(span)
    return out


def synthesise_workflow(spans: list[Span]) -> dict[str, Any]:
    """Build the workflow envelope JSON from spans of ONE trace.

    All spans MUST share the same `trace_id`. The function does NOT
    re-group; pass the result of `group_by_trace(...)[trace_id]`.

    Output shape (Q-100-014 envelope):
      - trace_id, started_at, ended_at, duration_ms
      - spans[]  — sorted chronologically, each carries parent_span_id
      - components_touched[]  — distinct component names, sorted
      - total_spans, errors (status>=500), warnings (400<=status<500)
      - verdict — "ok" | "warn" | "error"
      - root_span_id — span with empty parent_span_id (or first if none)
    """
    if not spans:
        raise ValueError("synthesise_workflow requires at least one span")

    trace_id = spans[0].trace_id
    if any(s.trace_id != trace_id for s in spans):
        raise ValueError(
            "synthesise_workflow expects spans of a single trace; "
            "use group_by_trace() to partition first"
        )

    sorted_spans = sorted(spans, key=lambda s: s.started_at)
    started_at = sorted_spans[0].started_at
    ended_at = max(s.ended_at for s in sorted_spans)
    duration_ms = (ended_at - started_at).total_seconds() * 1000.0

    components = sorted({s.component for s in sorted_spans if s.component})
    errors = sum(1 for s in sorted_spans if s.status_code >= 500)
    warnings = sum(1 for s in sorted_spans if 400 <= s.status_code < 500)
    if errors:
        verdict = "error"
    elif warnings:
        verdict = "warn"
    else:
        verdict = "ok"

    # Root: the span without a parent (the FIRST request entering the
    # platform from outside, or the trace's true origin). When multiple
    # parentless spans exist (rare — usually a multi-trace bug), pick
    # the chronological earliest so the envelope still makes sense.
    parentless = [s for s in sorted_spans if not s.parent_span_id]
    root_span_id = parentless[0].span_id if parentless else sorted_spans[0].span_id

    return {
        "trace_id": trace_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_ms": round(duration_ms, 3),
        "root_span_id": root_span_id,
        "spans": [_span_to_dict(s) for s in sorted_spans],
        "summary": {
            "components_touched": components,
            "total_spans": len(sorted_spans),
            "errors": errors,
            "warnings": warnings,
            "verdict": verdict,
        },
    }


def list_recent_traces(
    spans: Iterable[Span], *, limit: int = 10
) -> list[dict[str, Any]]:
    """Return compact one-line summaries of the most recent traces.

    Sorted by trace's last activity (`ended_at`) descending. Each entry
    contains: trace_id, started_at, duration_ms, total_spans,
    components_touched, verdict — enough for a developer to scan and
    pick the trace they want to drill into via
    `GET /workflows/<trace_id>`.
    """
    grouped = group_by_trace(spans)
    summaries: list[dict[str, Any]] = []
    for trace_id, trace_spans in grouped.items():
        env = synthesise_workflow(trace_spans)
        summaries.append(
            {
                "trace_id": trace_id,
                "started_at": env["started_at"],
                "ended_at": env["ended_at"],
                "duration_ms": env["duration_ms"],
                "total_spans": env["summary"]["total_spans"],
                "components_touched": env["summary"]["components_touched"],
                "verdict": env["summary"]["verdict"],
            }
        )
    summaries.sort(key=lambda s: s["ended_at"], reverse=True)
    return summaries[:limit]


def _span_to_dict(span: Span) -> dict[str, Any]:
    """Serialise a Span to the on-the-wire JSON shape (datetime → ISO string)."""
    out = asdict(span)
    out["started_at"] = span.started_at.isoformat()
    out["ended_at"] = span.ended_at.isoformat()
    out["operation"] = span.operation
    return out
