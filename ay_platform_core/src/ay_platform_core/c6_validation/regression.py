# =============================================================================
# File: regression.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/regression.py
# Description: Quality-regression detection between a run and the project's
#              previous completed run (D-017 / R-700-033). The per-entity
#              quality "time series" is realised incrementally : instead of a
#              stored series, each run compares itself to its predecessor and
#              emits ADVISORY findings where quality dropped — so a regression
#              surfaces exactly where it is read (in the run's own findings).
#
#              PURE function : same inputs → same findings (aside from the
#              metadata id/timestamp). The I/O (fetching the previous run) is
#              the service's responsibility, keeping this module unit-testable
#              without a database.
#
#              IMPORTANT (anti-feedback) : the ADVISORY findings produced here
#              are appended AFTER the deterministic (T1) verdict is graded, so
#              they never lower the very score the next run compares against.
#
# @relation implements:R-700-033
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from ay_platform_core.c6_validation.models import Finding, Severity

CHECK_ID = "quality-regression"

# A score drop smaller than this is treated as noise (no regression finding).
_SCORE_EPSILON = 1e-9


def _blocking_entities(findings: Sequence[Finding]) -> set[str]:
    return {
        f.entity_id
        for f in findings
        if f.severity is Severity.BLOCKING and f.entity_id
    }


def detect_regressions(
    *,
    run_id: str,
    domain: str,
    current_findings: Sequence[Finding],
    current_score: float,
    previous_run_id: str,
    previous_findings: Sequence[Finding],
    previous_score: float,
) -> list[Finding]:
    """Return ADVISORY findings for every quality regression vs the previous
    run (R-700-033) :

    - one per entity that **gained** blocking findings (was clean / advisory
      before, blocking now) — the per-entity signal ;
    - one overall finding when the run's deterministic verdict **score
      dropped** vs the previous run.

    Returns ``[]`` when nothing regressed (an improvement or a steady state
    never produces a finding)."""
    now = datetime.now(UTC)

    def _finding(message: str, *, entity_id: str | None) -> Finding:
        return Finding(
            finding_id=str(uuid.uuid4()),
            run_id=run_id,
            check_id=CHECK_ID,
            domain=domain,
            severity=Severity.ADVISORY,
            entity_id=entity_id,
            message=message,
            fix_hint=(
                "Compare this run with the previous one to locate the "
                "newly-introduced defect; the prior state was healthier."
            ),
            created_at=now,
        )

    findings: list[Finding] = []

    newly_blocking = _blocking_entities(current_findings) - _blocking_entities(
        previous_findings
    )
    findings.extend(
        _finding(
            (
                f"Entity {eid} regressed: it gained blocking finding(s) since "
                f"the previous completed run {previous_run_id}."
            ),
            entity_id=eid,
        )
        for eid in sorted(newly_blocking)
    )

    if current_score < previous_score - _SCORE_EPSILON:
        findings.append(
            _finding(
                (
                    f"Quality regression: the deterministic verdict score "
                    f"dropped {previous_score:.3f} → {current_score:.3f} versus "
                    f"the previous completed run {previous_run_id}."
                ),
                entity_id=None,
            )
        )

    return findings
