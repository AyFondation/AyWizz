# =============================================================================
# File: test_state_machine.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_state_machine.py
# Description: Unit tests for the pure state-machine decision function.
#              Pure: no I/O, no persistence — easy to exercise every
#              (phase, status, rounds) combination.
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c4_orchestrator.models import EscalationStatus, Phase
from ay_platform_core.c4_orchestrator.state import decide_transition, successor


@pytest.mark.unit
class TestSuccessor:
    def test_brainstorm_to_spec(self) -> None:
        assert successor(Phase.BRAINSTORM) == Phase.SPEC

    def test_plan_to_generate(self) -> None:
        assert successor(Phase.PLAN) == Phase.GENERATE

    def test_review_terminal(self) -> None:
        assert successor(Phase.REVIEW) is None


@pytest.mark.unit
class TestDecideTransition:
    def test_done_advances_to_next_phase(self) -> None:
        t = decide_transition(
            Phase.BRAINSTORM,
            EscalationStatus.DONE,
            enrichment_rounds_used=0,
        )
        assert t.next_phase == Phase.SPEC
        assert not t.run_completed
        assert not t.run_blocked
        assert not t.retry_phase

    def test_done_with_concerns_also_advances(self) -> None:
        t = decide_transition(
            Phase.SPEC,
            EscalationStatus.DONE_WITH_CONCERNS,
            enrichment_rounds_used=0,
        )
        assert t.next_phase == Phase.PLAN

    def test_review_done_completes_run(self) -> None:
        t = decide_transition(
            Phase.REVIEW,
            EscalationStatus.DONE,
            enrichment_rounds_used=0,
        )
        assert t.run_completed
        assert t.next_phase is None

    def test_needs_context_under_cap_retries(self) -> None:
        t = decide_transition(
            Phase.PLAN,
            EscalationStatus.NEEDS_CONTEXT,
            enrichment_rounds_used=1,
            enrichment_round_cap=3,
        )
        assert t.next_phase == Phase.PLAN
        assert t.retry_phase
        assert not t.run_blocked

    def test_needs_context_at_cap_blocks(self) -> None:
        t = decide_transition(
            Phase.PLAN,
            EscalationStatus.NEEDS_CONTEXT,
            enrichment_rounds_used=3,
            enrichment_round_cap=3,
        )
        assert t.run_blocked
        assert not t.retry_phase

    def test_blocked_halts_run(self) -> None:
        t = decide_transition(
            Phase.GENERATE,
            EscalationStatus.BLOCKED,
            enrichment_rounds_used=0,
        )
        assert t.run_blocked
        assert not t.retry_phase

    def test_unknown_status_raises(self) -> None:
        with pytest.raises(ValueError):
            decide_transition(
                Phase.BRAINSTORM,
                "NOT_A_REAL_STATUS",  # type: ignore[arg-type]
                enrichment_rounds_used=0,
            )
