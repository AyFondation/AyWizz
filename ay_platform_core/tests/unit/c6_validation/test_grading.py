# =============================================================================
# File: test_grading.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_grading.py
# Description: Unit tests for the D-017 T1 deterministic grading (R-700-031) —
#              the severity-weighted score is a pure function of the findings ;
#              the Verdict carries DETERMINISTIC provenance, confidence 1.0,
#              and cites the blocking/advisory findings as evidence.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ay_platform_core.c6_validation.grading import (
    deterministic_score,
    grade_deterministic,
)
from ay_platform_core.c6_validation.models import (
    Finding,
    Severity,
    VerdictMethod,
)

pytestmark = pytest.mark.unit


def _finding(fid: str, severity: Severity) -> Finding:
    return Finding(
        finding_id=fid,
        run_id="run-1",
        check_id="chk",
        domain="code",
        severity=severity,
        message="m",
        created_at=datetime.now(UTC),
    )


class TestDeterministicScore:
    def test_clean_run_is_one(self) -> None:
        assert deterministic_score([]) == 1.0
        assert deterministic_score([_finding("f", Severity.INFO)]) == 1.0

    def test_blocking_costs_half(self) -> None:
        assert deterministic_score([_finding("f", Severity.BLOCKING)]) == 0.5

    def test_advisory_costs_a_tenth(self) -> None:
        assert deterministic_score([_finding("f", Severity.ADVISORY)]) == pytest.approx(0.9)

    def test_floored_at_zero(self) -> None:
        findings = [_finding(f"f{i}", Severity.BLOCKING) for i in range(3)]
        assert deterministic_score(findings) == 0.0  # 3*0.5 = 1.5 → floored

    def test_pure(self) -> None:
        findings = [_finding("a", Severity.BLOCKING), _finding("b", Severity.INFO)]
        assert deterministic_score(findings) == deterministic_score(findings)


class TestGradeDeterministic:
    def test_verdict_envelope(self) -> None:
        findings = [
            _finding("a", Severity.BLOCKING),
            _finding("b", Severity.ADVISORY),
            _finding("c", Severity.INFO),
        ]
        v = grade_deterministic(findings, run_id="run-1", domain="code")
        assert v.method is VerdictMethod.DETERMINISTIC
        assert v.confidence == 1.0
        assert v.run_id == "run-1"
        assert v.domain == "code"
        assert v.score == pytest.approx(0.4)  # 1 - (0.5 + 0.1)
        # Evidence cites the blocking + advisory findings, not the info one.
        assert set(v.evidence) == {"a", "b"}
        assert "1 blocking" in v.rationale and "1 advisory" in v.rationale

    def test_clean_run_scores_one_with_no_evidence(self) -> None:
        v = grade_deterministic([], run_id="r", domain="code")
        assert v.score == 1.0
        assert v.evidence == []
