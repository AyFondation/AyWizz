# src/logging/context.py — v1
"""Contextual logging support — attach document_id, run_id, agent to log records.

See spec §23.3 for log format details.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

# Context variables for structured logging — set per-document execution.
_document_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "document_id", default=None
)
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)
_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agent", default=None
)
_step: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "step", default=None
)


@dataclass
class LogContext:
    """Immutable snapshot of current logging context."""

    document_id: str | None = None
    run_id: str | None = None
    agent: str | None = None
    step: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return non-None fields as dict for JSON log injection."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


def get_context() -> LogContext:
    """Snapshot current context variables."""
    return LogContext(
        document_id=_document_id.get(),
        run_id=_run_id.get(),
        agent=_agent.get(),
        step=_step.get(),
    )


def set_document_context(document_id: str, run_id: str) -> None:
    """Set document-level context (called once per document execution)."""
    _document_id.set(document_id)
    _run_id.set(run_id)


def set_agent_context(agent: str, step: str | None = None) -> None:
    """Set agent-level context (called per agent execution)."""
    _agent.set(agent)
    _step.set(step)


def clear_context() -> None:
    """Reset all context variables."""
    _document_id.set(None)
    _run_id.set(None)
    _agent.set(None)
    _step.set(None)
