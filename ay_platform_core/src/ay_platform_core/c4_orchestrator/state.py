# =============================================================================
# File: state.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/state.py
# Description: State machine for the five-phase pipeline. Declares the legal
#              transitions from each (phase, status) pair so that phase
#              advancement is decided in one place. The actual execution of
#              transitions (persistence, events, gate checks) lives in
#              service.py.
#
# @relation implements:R-200-001
# @relation implements:R-200-003
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass

from ay_platform_core.c4_orchestrator.models import EscalationStatus, Phase

# Successor of each non-terminal phase. BRAINSTORM → SPEC → PLAN → GENERATE →
# REVIEW. Review has no successor; a DONE review transitions the run to
# COMPLETED, handled at the service layer.
_PHASE_SUCCESSOR: dict[Phase, Phase | None] = {
    Phase.BRAINSTORM: Phase.SPEC,
    Phase.SPEC: Phase.PLAN,
    Phase.PLAN: Phase.GENERATE,
    Phase.GENERATE: Phase.REVIEW,
    Phase.REVIEW: None,
}


@dataclass(frozen=True, slots=True)
class Transition:
    """Outcome of a state-machine evaluation."""

    next_phase: Phase | None  # None means "stay in current phase" or "run complete"
    run_completed: bool = False
    run_blocked: bool = False
    retry_phase: bool = False  # NEEDS_CONTEXT enrichment round


def successor(phase: Phase) -> Phase | None:
    """Return the phase that follows `phase`, or None if terminal."""
    return _PHASE_SUCCESSOR[phase]


def decide_transition(
    current_phase: Phase,
    status: EscalationStatus,
    *,
    enrichment_rounds_used: int,
    enrichment_round_cap: int = 3,
) -> Transition:
    """Pure decision function — consumes a completion, returns the next move.

    - `DONE` / `DONE_WITH_CONCERNS` on a non-terminal phase → advance.
    - `DONE` on REVIEW → run completed (R-200-003).
    - `NEEDS_CONTEXT` with rounds remaining → retry same phase.
    - `NEEDS_CONTEXT` over cap → BLOCKED (R-200-040).
    - `BLOCKED` → halt run (R-200-003).

    Does NOT touch persistence, gates, or events — those belong to
    service.py so the state machine stays deterministic and testable.
    """
    if status in (EscalationStatus.DONE, EscalationStatus.DONE_WITH_CONCERNS):
        next_phase = successor(current_phase)
        if next_phase is None:
            return Transition(next_phase=None, run_completed=True)
        return Transition(next_phase=next_phase)
    if status == EscalationStatus.NEEDS_CONTEXT:
        if enrichment_rounds_used < enrichment_round_cap:
            return Transition(next_phase=current_phase, retry_phase=True)
        return Transition(next_phase=None, run_blocked=True)
    if status == EscalationStatus.BLOCKED:
        return Transition(next_phase=None, run_blocked=True)
    raise ValueError(f"Unknown escalation status: {status!r}")
