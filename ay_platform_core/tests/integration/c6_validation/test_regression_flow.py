# =============================================================================
# File: test_regression_flow.py
# Version: 1
# Path: ay_platform_core/tests/integration/c6_validation/test_regression_flow.py
# Description: Integration test for quality-regression detection (R-700-033)
#              against REAL ArangoDB + MinIO. Runs two validations for the same
#              project in one fresh DB : a clean first run (no predecessor → no
#              regression), then a degraded second run that loses an
#              implementation — which SHALL emit a `quality-regression`
#              ADVISORY finding (per-entity + overall score drop).
#
# @relation validates:R-700-033
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    RunStatus,
    RunTriggerRequest,
    Severity,
)
from ay_platform_core.c6_validation.service import ValidationService

pytestmark = pytest.mark.integration


def _req(entity_id: str, *, status: str = "approved", type_: str = "R") -> dict[str, Any]:
    return {"entity_id": entity_id, "status": status, "type": type_}


_CLEAN_ARTIFACTS = [
    CodeArtifact(path="src/impl.py", content="# @relation implements:R-100-001\n"),
    CodeArtifact(
        path="tests/unit/test_impl.py",
        content="# @relation validates:R-100-001\n",
        is_test=True,
    ),
]
# R-100-001 loses its implementing module → req-without-code blocking on it.
_DEGRADED_ARTIFACTS = [
    CodeArtifact(path="src/empty.py", content="def bare(): pass\n"),
]


async def _run(svc: ValidationService, artifacts: list[CodeArtifact]) -> Any:
    payload = RunTriggerRequest(domain="code", project_id="demo")
    return await svc.execute_run_sync(
        payload, requirements=[_req("R-100-001")], artifacts=artifacts
    )


@pytest.mark.asyncio
async def test_regression_detected_on_second_degraded_run(
    c6_service: ValidationService,
) -> None:
    # Run 1 — clean. First run of the project ⇒ no predecessor ⇒ no regression.
    run1 = await _run(c6_service, _CLEAN_ARTIFACTS)
    assert run1.status == RunStatus.COMPLETED
    assert run1.findings_count.blocking == 0
    assert run1.findings_count.advisory == 0  # no regression on the first run
    assert run1.verdict is not None and run1.verdict.score == 1.0

    # Run 2 — degraded : R-100-001 lost its implementation.
    run2 = await _run(c6_service, _DEGRADED_ARTIFACTS)
    assert run2.status == RunStatus.COMPLETED
    assert run2.findings_count.blocking >= 1
    assert run2.verdict is not None and run2.verdict.score < 1.0

    page = await c6_service.list_findings(run2.run_id, limit=1000)
    regressions = [f for f in page.items if f.check_id == "quality-regression"]
    assert regressions, "expected a quality-regression finding on the degraded run"
    assert all(f.severity is Severity.ADVISORY for f in regressions)
    # One regression cites R-100-001 (it gained a blocking finding).
    assert any(f.entity_id == "R-100-001" for f in regressions)
    assert run2.findings_count.advisory >= 1


@pytest.mark.asyncio
async def test_no_regression_when_quality_holds(
    c6_service: ValidationService,
) -> None:
    """Two identical clean runs ⇒ steady state ⇒ the second run emits NO
    regression finding."""
    await _run(c6_service, _CLEAN_ARTIFACTS)
    run2 = await _run(c6_service, _CLEAN_ARTIFACTS)
    page = await c6_service.list_findings(run2.run_id, limit=1000)
    assert [f for f in page.items if f.check_id == "quality-regression"] == []
    assert run2.findings_count.advisory == 0
