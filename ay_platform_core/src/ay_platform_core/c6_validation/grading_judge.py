# =============================================================================
# File: grading_judge.py
# Version: 1
# Path: ay_platform_core/src/ay_platform_core/c6_validation/grading_judge.py
# Description: T3 (JUDGED) grading for the D-017 evaluation harness
#              (R-700-032). An LLM-as-judge grades the run's artifacts against
#              its requirements on a qualitative [0,1] scale that the
#              deterministic (T1) and reference (T2) tiers cannot capture
#              (clarity, completeness, apparent requirement-satisfaction).
#
#              The judge is reached through C8 (agent route `c6-judge`), which
#              SHALL resolve to a model family DIFFERENT from the generator's
#              (D-011, anti self-preference bias). The grade is mapped onto the
#              shared `Verdict` envelope (R-700-030) with method=JUDGED and
#              confidence < 1.0 (an LLM grade is never certain).
#
#              BEST-EFFORT : any provider error or unparseable reply yields
#              `None` — the caller keeps the T1 verdict and completes the run.
#              T3 SHALL NOT fail a run nor suppress the deterministic grade.
#
# @relation implements:R-700-032
# =============================================================================

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    Verdict,
    VerdictMethod,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)

# Prompt budget guards — keep the judge call cost-bounded regardless of corpus.
_MAX_ARTIFACTS = 20
_MAX_ARTIFACT_CHARS = 4000
_MAX_REQUIREMENTS = 50
# An LLM grade is never fully certain ; cap confidence below 1.0 so a JUDGED
# verdict is never mistaken for a DETERMINISTIC one (which is exactly 1.0).
_MAX_JUDGE_CONFIDENCE = 0.99

_SYSTEM_RUBRIC = (
    "You are an impartial software-quality judge. Given a set of REQUIREMENTS "
    "and the CODE ARTIFACTS produced for them, grade how well the artifacts "
    "satisfy the requirements on clarity, completeness, and apparent "
    "correctness. You are NOT executing the code — judge only what is "
    "observable in the text. Reply with STRICT JSON and nothing else, of "
    "exactly this shape:\n"
    '{"score": <float 0..1>, "confidence": <float 0..1>, '
    '"rationale": "<one or two sentences>", '
    '"evidence": ["<artifact path or entity id>", ...]}\n'
    "score 1.0 = fully satisfies; 0.0 = unrelated or empty. Be calibrated: "
    "reserve >0.9 for genuinely complete, well-structured work."
)


def _assistant_text(response: object) -> str:
    """Extract assistant text, tolerant of the OpenAI multi-modal (list-of-
    parts) content shape and of a `content=null` tool-call reply."""
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    content = getattr(choices[0].message, "content", None)
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return content.strip() if isinstance(content, str) else ""


def _clamp01(value: Any, *, default: float) -> float:
    """Coerce `value` to a float in [0,1] ; fall back to `default` when it is
    not numeric."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _parse_judge_json(text: str) -> dict[str, Any] | None:
    """Lenient extraction of the first JSON object in the judge reply. Returns
    None when nothing parseable is found (judges occasionally wrap JSON in
    prose or code fences)."""
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _summarise_requirements(requirements: Sequence[dict[str, Any]]) -> str:
    rows: list[str] = []
    for row in list(requirements)[:_MAX_REQUIREMENTS]:
        rid = row.get("entity_id") or row.get("id") or "?"
        title = row.get("title") or row.get("text") or row.get("description") or ""
        rows.append(f"- {rid}: {str(title)[:200]}")
    return "\n".join(rows) if rows else "(no requirements provided)"


def _summarise_artifacts(artifacts: Sequence[CodeArtifact]) -> str:
    blocks: list[str] = []
    for art in list(artifacts)[:_MAX_ARTIFACTS]:
        blocks.append(f"### {art.path}\n{art.content[:_MAX_ARTIFACT_CHARS]}")
    return "\n\n".join(blocks) if blocks else "(no artifacts provided)"


async def grade_judged(
    *,
    llm_client: LLMGatewayClient,
    run_id: str,
    domain: str,
    requirements: Sequence[dict[str, Any]],
    artifacts: Sequence[CodeArtifact],
    agent_name: str,
    project_id: str,
    tenant_id: str = "",
    session_id: str | None = None,
) -> Verdict | None:
    """Build the T3 (`JUDGED`) verdict via an LLM-as-judge (R-700-032).

    Best-effort : returns ``None`` when the judge errors or returns an
    unparseable reply — the caller keeps the run's T1 verdict. The judge model
    is selected by the C8 agent route ``agent_name``, which SHALL point at a
    non-generator family (D-011).
    """
    user_payload = (
        "REQUIREMENTS:\n"
        + _summarise_requirements(requirements)
        + "\n\nCODE ARTIFACTS:\n"
        + _summarise_artifacts(artifacts)
        + "\n\nReturn the JSON grade now."
    )
    try:
        response = await llm_client.chat_completion(
            ChatCompletionRequest(
                messages=[
                    ChatMessage(role=ChatRole.SYSTEM, content=_SYSTEM_RUBRIC),
                    ChatMessage(role=ChatRole.USER, content=user_payload),
                ],
                stream=False,
                temperature=0.0,
                response_format={"type": "json_object"},
            ),
            agent_name=agent_name,
            session_id=session_id or f"judge:{run_id}",
            tenant_id=tenant_id or None,
            project_id=project_id,
        )
    except Exception:
        # Best-effort : a judge failure must never break the run (R-700-032).
        return None

    parsed = _parse_judge_json(_assistant_text(response))
    if parsed is None:
        return None

    rationale = parsed.get("rationale")
    rationale_str = (
        str(rationale)[:1000]
        if rationale is not None
        else "LLM-as-judge grade (no rationale returned)."
    )
    evidence_raw = parsed.get("evidence")
    evidence = (
        [str(e) for e in evidence_raw] if isinstance(evidence_raw, list) else []
    )
    return Verdict(
        verdict_id=f"vd-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        domain=domain,
        method=VerdictMethod.JUDGED,
        score=_clamp01(parsed.get("score"), default=0.0),
        confidence=min(
            _clamp01(parsed.get("confidence"), default=0.5), _MAX_JUDGE_CONFIDENCE
        ),
        rationale=rationale_str,
        evidence=evidence,
        created_at=datetime.now(UTC),
    )
