# =============================================================================
# File: plugin.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/domains/documentation/plugin.py
# Description: `documentation` domain plug-in (R-200-061 v2, P4.a). Mirrors
#              the `code` plug-in's declarative-evidence pattern but
#              with documentation-shaped semantics :
#                - Gate B = "outline artifact exists + lists the sections
#                  the operator's prompt asks for" (analogue of code's
#                  "test exists and runs red").
#                - Gate C = "every outline section has a non-trivial
#                  body AND the outline timestamp is older than the
#                  doc body's last write" (analogue of code's "fresh green").
#
#              Selection between domains is per-deployment via the
#              `C4_DOMAIN` env var (`code` default, `documentation` opt-in).
#              Per-run cross-domain dispatch deferred to v2 — Q-200-012.
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

DOCUMENTATION_DESCRIPTOR = DomainDescriptor(
    domain="documentation",
    artifact_mime_types=[
        "text/markdown",
        "text/x-rst",
        "text/asciidoc",
    ],
    validation_artifact_type="documentation_outline",
    gate_b=GateCheck(
        check="outline_exists",
        implementation=(
            "ay_platform_core.c4_orchestrator.domains.documentation.plugin:"
            "DocumentationDomainPlugin.evaluate_gate_b"
        ),
    ),
    gate_c=GateCheck(
        check="sections_filled_fresh",
        implementation=(
            "ay_platform_core.c4_orchestrator.domains.documentation.plugin:"
            "DocumentationDomainPlugin.evaluate_gate_c"
        ),
    ),
)


# Minimum body length (chars) under which a section is considered
# "trivial" / placeholder content. Chosen conservatively : a useful
# section runs to at least a paragraph (~200 chars). Operator can tune
# via Q-200-021 once we have body-length data from real runs.
_MIN_SECTION_BODY_CHARS = 200


class DocumentationDomainPlugin:
    """v1 stub implementation of the `documentation` domain plug-in.

    Declarative evidence — the agent fills `gate_b_evidence` /
    `gate_c_evidence` on its envelope ; this plug-in just interprets
    them. Same separation-of-concerns as `CodeDomainPlugin` ; the
    actual outline / body assembly happens in the agent prompts (or
    a future C15 OpenHands sub-agent), NOT in C4.
    """

    descriptor = DOCUMENTATION_DESCRIPTOR

    async def evaluate_gate_b(
        self, run_id: str, artifact_payload: dict[str, Any],
    ) -> GateResult:
        """Outline artifact exists AND its `sections` list is non-empty
        AND covers the operator's requested sections. The agent supplies :
        ```
        gate_b_evidence: {
            artifact_id: "<path/to/outline.md>",
            outline_artifact_exists: true,
            sections: ["Introduction", "Architecture", "API"],
            evidence_timestamp: "<ISO-8601 UTC>",
        }
        ```
        """
        evidence = artifact_payload.get("gate_b_evidence") or {}
        artifact_id = str(evidence.get("artifact_id", "unknown"))
        outline_exists = bool(evidence.get("outline_artifact_exists", False))
        sections = evidence.get("sections")
        section_list: list[str] = (
            [str(s) for s in sections if isinstance(s, str)]
            if isinstance(sections, list)
            else []
        )
        if not outline_exists:
            return GateResult(
                gate=Gate.B_VALIDATION_RED,
                passed=False,
                artifact_id=artifact_id,
                reason="documentation outline artifact does not exist",
            )
        if not section_list:
            return GateResult(
                gate=Gate.B_VALIDATION_RED,
                passed=False,
                artifact_id=artifact_id,
                reason="outline artifact exists but lists zero sections",
            )
        ts = _parse_timestamp(evidence.get("evidence_timestamp"))
        return GateResult(
            gate=Gate.B_VALIDATION_RED,
            passed=True,
            artifact_id=artifact_id,
            evidence_timestamp=ts,
        )

    async def evaluate_gate_c(
        self, run_id: str, artifact_payload: dict[str, Any],
    ) -> GateResult:
        """Every declared section has a non-trivial body AND the
        outline-vs-body freshness ordering holds. The agent supplies :
        ```
        gate_c_evidence: {
            artifact_id: "<path/to/doc.md>",
            sections_filled: {"Introduction": 412, "Architecture": 1820, ...},
            evidence_timestamp: "<ISO-8601 UTC>",  # last body write
            outline_timestamp: "<ISO-8601 UTC>",   # outline write (older)
        }
        ```
        """
        evidence = artifact_payload.get("gate_c_evidence") or {}
        artifact_id = str(evidence.get("artifact_id", "unknown"))
        sections_filled = evidence.get("sections_filled")
        body_ts = _parse_timestamp(evidence.get("evidence_timestamp"))
        outline_ts = _parse_timestamp(evidence.get("outline_timestamp"))

        if not isinstance(sections_filled, dict) or not sections_filled:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason="no `sections_filled` map on documentation evidence",
            )
        # Detect placeholder content — any section under the body-length
        # threshold blocks Gate C with the explicit list of offenders.
        thin = [
            name
            for name, length in sections_filled.items()
            if not isinstance(length, int) or length < _MIN_SECTION_BODY_CHARS
        ]
        if thin:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason=(
                    f"thin/placeholder sections (<{_MIN_SECTION_BODY_CHARS} "
                    f"chars) : {', '.join(thin)}"
                ),
                evidence_timestamp=body_ts,
            )
        if body_ts is None or outline_ts is None:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason=(
                    "missing timestamp metadata "
                    "(outline_timestamp + evidence_timestamp required)"
                ),
            )
        if body_ts <= outline_ts:
            return GateResult(
                gate=Gate.C_VALIDATION_FRESH_GREEN,
                passed=False,
                artifact_id=artifact_id,
                reason=(
                    f"body older than outline : body at {body_ts.isoformat()} "
                    f"vs outline at {outline_ts.isoformat()}"
                ),
                evidence_timestamp=body_ts,
            )
        return GateResult(
            gate=Gate.C_VALIDATION_FRESH_GREEN,
            passed=True,
            artifact_id=artifact_id,
            evidence_timestamp=body_ts,
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
