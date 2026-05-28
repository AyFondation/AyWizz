# =============================================================================
# File: plugin.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/code/plugin.py
# Description: `code` domain plug-in (R-200-061). v1 implementation uses a
#              declarative payload: the agent's envelope is expected to
#              include `gate_b_evidence` / `gate_c_evidence` fields that
#              enumerate the validation artifacts and their claimed state.
#              This keeps C4 out of the business of running pytest itself
#              (that's deferred behind a feature flag — spawning pytest
#              in the orchestrator pod is both slow and flaky).
#
#              When the real K8s sub-agent dispatcher lands, Gate B and
#              Gate C will instead consume artefacts produced by the
#              pod (result JSON written to MinIO under `c4-runs/<id>/`).
#              The protocol stays identical.
#
# @relation implements:R-200-011
# @relation implements:R-200-012
# @relation implements:R-200-061
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ay_platform_core.c4_orchestrator.models import (
    DomainDescriptor,
    Gate,
    GateCheck,
    GateResult,
)

CODE_DESCRIPTOR = DomainDescriptor(
    domain="code",
    artifact_mime_types=[
        "text/x-python",
        "text/x-typescript",
        "text/x-rust",
    ],
    validation_artifact_type="pytest_test",
    gate_b=GateCheck(
        check="validation_runs_red",
        implementation=(
            "ay_platform_core.c4_orchestrator.domains.code.plugin:"
            "CodeDomainPlugin.evaluate_gate_b"
        ),
    ),
    gate_c=GateCheck(
        check="validation_runs_green_fresh",
        implementation=(
            "ay_platform_core.c4_orchestrator.domains.code.plugin:"
            "CodeDomainPlugin.evaluate_gate_c"
        ),
    ),
)


class CodeDomainPlugin:
    """v1 stub implementation of the code domain plug-in.

    Both gate evaluators inspect a declarative `evidence` payload the
    agent MUST provide. This separates the orchestrator's concern (gate
    semantics) from the execution concern (running the validation
    artifact) — the latter moves to the sub-agent pod in v2.
    """

    descriptor = CODE_DESCRIPTOR

    async def evaluate_gate_b(
        self, run_id: str, artifact_payload: dict[str, Any]
    ) -> GateResult:
        evidence = artifact_payload.get("gate_b_evidence") or {}
        artifact_id = str(evidence.get("artifact_id", "unknown"))
        validation_exists = bool(evidence.get("validation_artifact_exists", False))
        runs_red = bool(evidence.get("validation_runs_red", False))

        if not validation_exists:
            return GateResult(
                gate=Gate.B_VALIDATION_RED,
                passed=False,
                artifact_id=artifact_id,
                reason="validation artifact does not exist",
            )
        if not runs_red:
            return GateResult(
                gate=Gate.B_VALIDATION_RED,
                passed=False,
                artifact_id=artifact_id,
                reason="validation artifact exists but does not run red",
            )
        ts = _parse_timestamp(evidence.get("evidence_timestamp"))
        return GateResult(
            gate=Gate.B_VALIDATION_RED,
            passed=True,
            artifact_id=artifact_id,
            evidence_timestamp=ts,
        )

    async def evaluate_gate_c(
        self, run_id: str, artifact_payload: dict[str, Any]
    ) -> GateResult:
        evidence = artifact_payload.get("gate_c_evidence") or {}
        artifact_id = str(evidence.get("artifact_id", "unknown"))
        runs_green = bool(evidence.get("validation_runs_green", False))
        evidence_ts = _parse_timestamp(evidence.get("evidence_timestamp"))
        last_artifact_ts = _parse_timestamp(evidence.get("last_artifact_write"))

        if not runs_green:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason="validation artifact not passing",
            )
        if evidence_ts is None or last_artifact_ts is None:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason="missing timestamp metadata on evidence",
            )
        if evidence_ts <= last_artifact_ts:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason=(
                    f"stale evidence: validation ran at {evidence_ts.isoformat()} "
                    f"but last artifact write was at {last_artifact_ts.isoformat()}"
                ),
                evidence_timestamp=evidence_ts,
            )
        return GateResult(
            gate=Gate.C_VALIDATION_FRESH_GREEN,
            passed=True,
            artifact_id=artifact_id,
            evidence_timestamp=evidence_ts,
        )


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
