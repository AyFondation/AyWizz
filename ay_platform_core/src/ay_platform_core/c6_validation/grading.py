# =============================================================================
# File: grading.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/grading.py
# Description: T1 (deterministic) grading for the D-017 evaluation harness
#              (R-700-031). Turns a run's binary findings into a graded,
#              provenance-tagged `Verdict` (method=DETERMINISTIC, confidence
#              1.0). The SCORE is a PURE function of the findings (severity-
#              weighted) so it is reproducible ; `verdict_id`/`created_at` are
#              metadata. T2 (reference) and T3 (judged) tiers extend the same
#              envelope and are separate modules.
#
# @relation implements:R-700-031
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from ay_platform_core.c6_validation.models import (
    Finding,
    Severity,
    Verdict,
    VerdictMethod,
)

# Per-finding penalty on the [0,1] score. A blocking finding costs more than
# an advisory ; info findings don't lower the grade (they're observations).
_BLOCKING_WEIGHT = 0.5
_ADVISORY_WEIGHT = 0.1


def deterministic_score(findings: Sequence[Finding]) -> float:
    """Severity-weighted coherence score in [0,1]. Pure : same findings →
    same score. 1.0 = no blocking/advisory findings."""
    penalty = sum(
        _BLOCKING_WEIGHT if f.severity is Severity.BLOCKING
        else _ADVISORY_WEIGHT if f.severity is Severity.ADVISORY
        else 0.0
        for f in findings
    )
    return max(0.0, 1.0 - penalty)


def grade_deterministic(
    findings: Sequence[Finding], *, run_id: str, domain: str
) -> Verdict:
    """Build the T1 (`DETERMINISTIC`) verdict for a run (R-700-031). Cites the
    blocking/advisory findings as evidence ; confidence 1.0 (deterministic)."""
    blocking = sum(1 for f in findings if f.severity is Severity.BLOCKING)
    advisory = sum(1 for f in findings if f.severity is Severity.ADVISORY)
    info = sum(1 for f in findings if f.severity is Severity.INFO)
    return Verdict(
        verdict_id=f"vd-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        domain=domain,
        method=VerdictMethod.DETERMINISTIC,
        score=deterministic_score(findings),
        confidence=1.0,
        rationale=f"{blocking} blocking, {advisory} advisory, {info} info finding(s)",
        evidence=[
            f.finding_id
            for f in findings
            if f.severity in (Severity.BLOCKING, Severity.ADVISORY)
        ],
        created_at=datetime.now(UTC),
    )
