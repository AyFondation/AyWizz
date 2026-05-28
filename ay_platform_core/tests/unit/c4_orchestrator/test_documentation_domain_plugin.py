# =============================================================================
# File: test_documentation_domain_plugin.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_documentation_domain_plugin.py
# Description: Unit tests for the `documentation` domain plug-in (P4.a).
#              Mirrors `test_code_domain_plugin.py` structure : every
#              gate-eval branch covered.
#
# @relation validates:R-200-011
# @relation validates:R-200-012
# @relation validates:R-200-061
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ay_platform_core.c4_orchestrator.domains.documentation.plugin import (
    DocumentationDomainPlugin,
)
from ay_platform_core.c4_orchestrator.models import Gate


@pytest.fixture
def plugin() -> DocumentationDomainPlugin:
    return DocumentationDomainPlugin()


# ---------------------------------------------------------------------------
# Gate B — outline artifact + sections list
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestGateB:
    async def test_missing_evidence_block_fails(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_b("run-1", {})
        assert not result.passed
        assert result.gate == Gate.B_VALIDATION_RED
        assert "does not exist" in (result.reason or "")

    async def test_outline_missing_fails(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_b(
            "run-1",
            {
                "gate_b_evidence": {
                    "artifact_id": "outline.md",
                    "outline_artifact_exists": False,
                },
            },
        )
        assert not result.passed
        assert "does not exist" in (result.reason or "")

    async def test_outline_with_zero_sections_fails(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_b(
            "run-1",
            {
                "gate_b_evidence": {
                    "artifact_id": "outline.md",
                    "outline_artifact_exists": True,
                    "sections": [],
                },
            },
        )
        assert not result.passed
        assert "zero sections" in (result.reason or "")

    async def test_outline_with_sections_passes(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_b(
            "run-1",
            {
                "gate_b_evidence": {
                    "artifact_id": "outline.md",
                    "outline_artifact_exists": True,
                    "sections": ["Intro", "Architecture", "API"],
                    "evidence_timestamp": "2026-05-20T10:00:00+00:00",
                },
            },
        )
        assert result.passed
        assert result.gate == Gate.B_VALIDATION_RED
        assert result.artifact_id == "outline.md"
        assert result.evidence_timestamp is not None

    async def test_non_list_sections_treated_as_empty(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_b(
            "run-1",
            {
                "gate_b_evidence": {
                    "artifact_id": "outline.md",
                    "outline_artifact_exists": True,
                    "sections": "not-a-list",  # malformed
                },
            },
        )
        assert not result.passed
        assert "zero sections" in (result.reason or "")


# ---------------------------------------------------------------------------
# Gate C — body fills outline AND freshness
# ---------------------------------------------------------------------------


def _evidence(
    *,
    sections_filled: dict[str, int] | None = None,
    body_ts: datetime | None = None,
    outline_ts: datetime | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "gate_c_evidence": {
            "artifact_id": "doc.md",
            "sections_filled": sections_filled
            or {"Intro": 600, "Architecture": 1200},
            "evidence_timestamp": (body_ts or now).isoformat(),
            "outline_timestamp": (
                outline_ts or now - timedelta(minutes=5)
            ).isoformat(),
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
class TestGateC:
    async def test_missing_sections_filled_fails(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        # Build the payload directly (without _evidence) so the inner
        # dict is statically typed for mypy ; clearer than del+ignore.
        result = await plugin.evaluate_gate_c(
            "run-1",
            {
                "gate_c_evidence": {
                    "artifact_id": "doc.md",
                    # NO sections_filled
                    "evidence_timestamp": "2026-05-20T10:00:00+00:00",
                    "outline_timestamp": "2026-05-20T09:55:00+00:00",
                },
            },
        )
        assert not result.passed
        assert "sections_filled" in (result.reason or "")

    async def test_thin_section_blocks_gate_c(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_c(
            "run-1",
            _evidence(sections_filled={"Intro": 600, "Stub": 12}),
        )
        assert not result.passed
        assert "Stub" in (result.reason or "")

    async def test_outline_newer_than_body_blocks(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        now = datetime.now(UTC)
        result = await plugin.evaluate_gate_c(
            "run-1",
            _evidence(
                body_ts=now - timedelta(minutes=10),
                outline_ts=now,
            ),
        )
        assert not result.passed
        assert "body older than outline" in (result.reason or "")

    async def test_missing_timestamps_blocks(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_c(
            "run-1",
            {
                "gate_c_evidence": {
                    "artifact_id": "doc.md",
                    "sections_filled": {"Intro": 600},
                    # NO timestamps
                },
            },
        )
        assert not result.passed
        assert "timestamp" in (result.reason or "")

    async def test_well_filled_and_fresh_passes(
        self, plugin: DocumentationDomainPlugin,
    ) -> None:
        result = await plugin.evaluate_gate_c("run-1", _evidence())
        assert result.passed
        assert result.gate == Gate.C_VALIDATION_FRESH_GREEN
        assert result.artifact_id == "doc.md"
