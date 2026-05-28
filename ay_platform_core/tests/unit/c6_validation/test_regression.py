# =============================================================================
# File: test_regression.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_regression.py
# Description: Unit tests for the pure quality-regression detector
#              (R-700-033). Covers per-entity new-blocking detection, the
#              overall score-drop signal, and the no-finding cases
#              (improvement / steady state).
#
# @relation validates:R-700-033
# =============================================================================

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from ay_platform_core.c6_validation.models import Finding, Severity
from ay_platform_core.c6_validation.regression import CHECK_ID, detect_regressions

pytestmark = pytest.mark.unit


def _f(entity_id: str | None, severity: Severity) -> Finding:
    return Finding(
        finding_id=str(uuid.uuid4()),
        run_id="r-cur",
        check_id="req-without-code",
        domain="code",
        severity=severity,
        entity_id=entity_id,
        message="m",
        created_at=datetime.now(UTC),
    )


def _detect(
    *,
    current: list[Finding],
    current_score: float,
    previous: list[Finding],
    previous_score: float,
) -> list[Finding]:
    return detect_regressions(
        run_id="r-cur",
        domain="code",
        current_findings=current,
        current_score=current_score,
        previous_run_id="r-prev",
        previous_findings=previous,
        previous_score=previous_score,
    )


def test_new_blocking_entity_and_score_drop() -> None:
    out = _detect(
        current=[_f("R-100-001", Severity.BLOCKING)],
        current_score=0.5,
        previous=[],
        previous_score=1.0,
    )
    # one per-entity finding (R-100-001) + one overall score-drop finding.
    assert len(out) == 2
    assert all(f.check_id == CHECK_ID for f in out)
    assert all(f.severity is Severity.ADVISORY for f in out)
    per_entity = [f for f in out if f.entity_id == "R-100-001"]
    assert len(per_entity) == 1
    overall = [f for f in out if f.entity_id is None]
    assert len(overall) == 1
    assert "0.500" in overall[0].message and "1.000" in overall[0].message


def test_score_drop_without_new_blocking() -> None:
    # No blocking entities at all, but the score dropped (e.g. advisory churn).
    out = _detect(
        current=[], current_score=0.9, previous=[], previous_score=1.0
    )
    assert len(out) == 1
    assert out[0].entity_id is None


def test_improvement_yields_nothing() -> None:
    out = _detect(
        current=[],
        current_score=1.0,
        previous=[_f("R-100-001", Severity.BLOCKING)],
        previous_score=0.5,
    )
    assert out == []


def test_steady_state_yields_nothing() -> None:
    out = _detect(
        current=[_f("R-100-001", Severity.BLOCKING)],
        current_score=0.5,
        previous=[_f("R-100-001", Severity.BLOCKING)],
        previous_score=0.5,
    )
    assert out == []


def test_already_blocking_entity_not_reported_again() -> None:
    # R-100-001 was already blocking; only the newly-blocking R-100-002 counts.
    out = _detect(
        current=[
            _f("R-100-001", Severity.BLOCKING),
            _f("R-100-002", Severity.BLOCKING),
        ],
        current_score=0.4,
        previous=[_f("R-100-001", Severity.BLOCKING)],
        previous_score=0.5,
    )
    per_entity = sorted(f.entity_id for f in out if f.entity_id is not None)
    assert per_entity == ["R-100-002"]


def test_advisory_to_blocking_transition_is_a_regression() -> None:
    # An entity that was only advisory before and is blocking now regressed.
    out = _detect(
        current=[_f("R-100-001", Severity.BLOCKING)],
        current_score=0.5,
        previous=[_f("R-100-001", Severity.ADVISORY)],
        previous_score=0.9,
    )
    assert any(f.entity_id == "R-100-001" for f in out)
