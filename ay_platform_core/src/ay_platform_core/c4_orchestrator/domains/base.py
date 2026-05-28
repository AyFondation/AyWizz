# =============================================================================
# File: base.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/base.py
# Description: Domain plug-in protocol for C4. Each production domain
#              (`code`, `documentation`, …) registers one implementation.
#              v1 ships only the `code` domain per R-200-061.
#
# @relation implements:R-200-060
# @relation implements:R-200-061
# =============================================================================

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ay_platform_core.c4_orchestrator.models import DomainDescriptor, GateResult


@runtime_checkable
class DomainPlugin(Protocol):
    """Concrete implementation of a production domain.

    Two gate-check methods correspond to R-200-011 (Gate B: validation
    artifact runs red) and R-200-012 (Gate C: validation runs green with
    fresh evidence). The orchestrator calls them at the phase
    boundaries declared by 200-SPEC Appendix 8.1.
    """

    descriptor: DomainDescriptor

    async def evaluate_gate_b(
        self, run_id: str, artifact_payload: dict[str, Any]
    ) -> GateResult:
        """Validation artifact exists AND currently fails as expected.

        `artifact_payload` is the `output` field of the implementer's
        agent completion. Its interpretation is domain-specific.
        """
        ...

    async def evaluate_gate_c(
        self, run_id: str, artifact_payload: dict[str, Any]
    ) -> GateResult:
        """Validation artifact passes AND the evidence is fresher than
        the last production-artifact write (R-200-012)."""
        ...
