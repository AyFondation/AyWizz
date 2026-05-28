# =============================================================================
# File: test_code_domain_plugin.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_code_domain_plugin.py
# Description: Unit tests for the `code` domain plug-in gate evaluators.
#              Exercises every branch of the declarative evidence payload.
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ay_platform_core.c4_orchestrator.domains.code.plugin import CodeDomainPlugin
from ay_platform_core.c4_orchestrator.models import Gate


@pytest.fixture
def plugin() -> CodeDomainPlugin:
    return CodeDomainPlugin()


@pytest.mark.unit
@pytest.mark.asyncio
class TestGateB:
    async def test_missing_evidence_block_fails(self, plugin: CodeDomainPlugin) -> None:
        result = await plugin.evaluate_gate_b("run-1", {})
        assert not result.passed
        assert result.gate == Gate.B_VALIDATION_RED
        assert "does not exist" in (result.reason or "")

    async def test_validation_not_red_fails(self, plugin: CodeDomainPlugin) -> None:
        result = await plugin.evaluate_gate_b("run-1", {
            "gate_b_evidence": {
                "artifact_id": "add_user",
                "validation_artifact_exists": True,
                "validation_runs_red": False,
            },
        })
        assert not result.passed
        assert "does not run red" in (result.reason or "")

    async def test_passing_evidence_accepted(self, plugin: CodeDomainPlugin) -> None:
        result = await plugin.evaluate_gate_b("run-1", {
            "gate_b_evidence": {
                "artifact_id": "add_user",
                "validation_artifact_exists": True,
                "validation_runs_red": True,
                "evidence_timestamp": "2026-04-23T12:00:00Z",
            },
        })
        assert result.passed
        assert result.artifact_id == "add_user"


@pytest.mark.unit
@pytest.mark.asyncio
class TestGateC:
    async def test_not_green_fails(self, plugin: CodeDomainPlugin) -> None:
        result = await plugin.evaluate_gate_c("run-1", {
            "gate_c_evidence": {
                "artifact_id": "add_user",
                "validation_runs_green": False,
            },
        })
        assert not result.passed
        assert result.gate == Gate.C_VALIDATION_FRESH_GREEN
        assert "not passing" in (result.reason or "")

    async def test_stale_evidence_rejected(self, plugin: CodeDomainPlugin) -> None:
        evidence_ts = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
        artifact_ts = evidence_ts + timedelta(minutes=1)  # written AFTER evidence
        result = await plugin.evaluate_gate_c("run-1", {
            "gate_c_evidence": {
                "artifact_id": "add_user",
                "validation_runs_green": True,
                "evidence_timestamp": evidence_ts.isoformat(),
                "last_artifact_write": artifact_ts.isoformat(),
            },
        })
        assert not result.passed
        assert "stale" in (result.reason or "")

    async def test_missing_timestamps_rejected(self, plugin: CodeDomainPlugin) -> None:
        result = await plugin.evaluate_gate_c("run-1", {
            "gate_c_evidence": {
                "artifact_id": "add_user",
                "validation_runs_green": True,
            },
        })
        assert not result.passed
        assert "timestamp" in (result.reason or "")

    async def test_fresh_green_passes(self, plugin: CodeDomainPlugin) -> None:
        artifact_ts = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
        evidence_ts = artifact_ts + timedelta(seconds=5)
        result = await plugin.evaluate_gate_c("run-1", {
            "gate_c_evidence": {
                "artifact_id": "add_user",
                "validation_runs_green": True,
                "evidence_timestamp": evidence_ts.isoformat(),
                "last_artifact_write": artifact_ts.isoformat(),
            },
        })
        assert result.passed
        assert result.evidence_timestamp == evidence_ts
