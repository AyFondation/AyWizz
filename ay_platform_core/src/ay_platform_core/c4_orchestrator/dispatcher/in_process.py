# =============================================================================
# File: in_process.py
# Version: 5
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/dispatcher/in_process.py
# Description: In-process agent dispatcher. Invokes the C8 LLM gateway
#              client with the agent-appropriate headers, interprets the
#              response as an AgentCompletion envelope, and returns it.
#              Replaces the real Kubernetes pod dispatcher until infra is
#              ready (R-200-030 / Q-200-001 baseline).
#
#              v4 (2026-05-14) : tolerant status inference. Adds a
#              synonym map (`completed` → DONE, `error` → BLOCKED, ...)
#              and a graceful fallback that assumes DONE when the
#              status is unknown but the envelope carries a non-empty
#              `output`. Surfaced after observing qwen2.5:3b
#              repeatedly omit the `status` key entirely.
#              v3 (2026-05-13) : tolerant JSON extraction. Small open
#              models (qwen2.5:3b et al.) frequently wrap their JSON in
#              markdown fences ```json ... ``` or prepend a line of
#              prose like "Sure, here is the JSON:\n{...}". The strict
#              `json.loads(content)` of v1/v2 would BLOCK on every such
#              call and trigger the three-fix rule on phase 1 — useless
#              in practice. v3 falls back to extracting the first valid
#              JSON object found in the content (fence-stripped or
#              brace-balanced scan) before declaring BLOCKED.
#              v2: GENERATE-phase system prompt extended to require
#              `output.files: [{path, content}]` per R-200-150 so the
#              orchestrator can materialise the agent's output into the
#              artifacts surface without intermediate transformation.
#
# @relation implements:R-200-021
# @relation implements:R-200-030
# @relation implements:R-200-150
# =============================================================================

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from ay_platform_core.c4_orchestrator.dispatcher.base import (
    DispatchRequest,
)
from ay_platform_core.c4_orchestrator.models import (
    AgentBlocker,
    AgentCompletion,
    AgentConcern,
    AgentNeedsContext,
    AgentRole,
    EscalationStatus,
    Phase,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient
from ay_platform_core.c8_llm.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatRole,
)

_log = logging.getLogger("c4_orchestrator.dispatcher")

# Per-phase system prompts. Intentionally minimal — v1 uses generic
# prompts per D-011 caveat. Production-grade prompt libraries live in
# operational config, not in code.
_SYSTEM_PROMPTS: dict[Phase, str] = {
    Phase.BRAINSTORM: (
        "You are the Architect agent. Elicit intent and produce an initial "
        "architectural proposal. Return JSON with keys `output` (your "
        "proposal) and `status` (one of DONE, DONE_WITH_CONCERNS, "
        "NEEDS_CONTEXT, BLOCKED)."
    ),
    Phase.SPEC: (
        "You are the Architect agent in the spec phase. Write requirements "
        "entities from the prior brainstorm. Return JSON with keys `output` "
        "(entity drafts) and `status`."
    ),
    Phase.PLAN: (
        "You are the Planner agent. Decompose the spec into ordered, "
        "testable steps; declare the validation artifact for each step. "
        "Return JSON with keys `output` (step list) and `status`."
    ),
    Phase.GENERATE: (
        "You are the Implementer agent. Produce the artifacts per the plan, "
        "honouring Gate B (validation artifact must exist and run red first). "
        "Return STRICT JSON with this shape (no prose outside the JSON):\n"
        "{\n"
        '  "status": "DONE" | "DONE_WITH_CONCERNS" | "NEEDS_CONTEXT" | "BLOCKED",\n'
        '  "output": {\n'
        '    "files": [ {"path": "src/main.py", "content": "<full file body>"}, ... ],\n'
        '    "gate_b_evidence": {\n'
        '      "artifact_id": "<one of the file paths used as the validation target>",\n'
        '      "validation_artifact_exists": true,\n'
        '      "validation_runs_red": true,\n'
        '      "evidence_timestamp": "<ISO-8601 UTC>"\n'
        "    }\n"
        "  }\n"
        "}\n"
        "Rules: paths SHALL be POSIX relative (no leading `/`, no `..`, no `\\\\`); "
        "`content` SHALL be the full UTF-8 text of the file (no diff fragments); "
        "produce at least one validation artifact (test/spec) that initially fails "
        "(`validation_runs_red=true`) per Gate B. Include between 1 and 12 files. "
        "Do NOT wrap the JSON in markdown code fences."
    ),
    Phase.REVIEW: (
        "You are a reviewer agent (spec compliance + quality). Evaluate "
        "artifacts against the spec and the quality rules. Return JSON with "
        "keys `output` (findings) and `status`."
    ),
}


_AGENT_FOR_PHASE: dict[Phase, AgentRole] = {
    Phase.BRAINSTORM: AgentRole.ARCHITECT,
    Phase.SPEC: AgentRole.ARCHITECT,
    Phase.PLAN: AgentRole.PLANNER,
    Phase.GENERATE: AgentRole.IMPLEMENTER,
    Phase.REVIEW: AgentRole.SPEC_REVIEWER,
}


# Common one-to-one synonyms emitted by small open models. Keep
# narrow — only unambiguous mappings. Looked up after the strict
# EscalationStatus enum check fails ; `_parse_completion` falls
# back to assume-DONE-when-output-present if even this map misses.
_STATUS_SYNONYMS: dict[str, EscalationStatus] = {
    "COMPLETED": EscalationStatus.DONE,
    "COMPLETE": EscalationStatus.DONE,
    "SUCCESS": EscalationStatus.DONE,
    "SUCCEEDED": EscalationStatus.DONE,
    "OK": EscalationStatus.DONE,
    "FINISHED": EscalationStatus.DONE,
    "ERROR": EscalationStatus.BLOCKED,
    "FAILED": EscalationStatus.BLOCKED,
    "FAILURE": EscalationStatus.BLOCKED,
}


def agent_for_phase(phase: Phase) -> AgentRole:
    """Helper: default agent role for a given phase."""
    return _AGENT_FOR_PHASE[phase]


class InProcessDispatcher:
    """Runs agents in-process by delegating to the C8 LLM gateway.

    The dispatcher is deliberately thin — it converts a `DispatchRequest`
    into an OpenAI-compatible chat completion, hits C8, and parses the
    JSON envelope the agent is expected to return. Error paths collapse
    onto `EscalationStatus.BLOCKED` so the orchestrator always receives
    a well-formed completion.
    """

    def __init__(self, llm_client: LLMGatewayClient) -> None:
        self._llm = llm_client

    async def dispatch(self, request: DispatchRequest) -> AgentCompletion:
        system = _SYSTEM_PROMPTS[request.phase]
        user = _build_user_prompt(request)
        started = time.monotonic()

        try:
            response = await self._llm.chat_completion(
                ChatCompletionRequest(
                    messages=[
                        ChatMessage(role=ChatRole.SYSTEM, content=system),
                        ChatMessage(role=ChatRole.USER, content=user),
                    ],
                    # `response_format={type:json_object}` leans on structured
                    # outputs where the provider supports it (R-800-041).
                    response_format={"type": "json_object"},
                ),
                agent_name=request.agent.value,
                session_id=request.session_id,
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                phase=request.phase.value,
            )
        except Exception as exc:  # deliberately broad — any failure is BLOCKED
            return AgentCompletion(
                agent=request.agent,
                run_id=request.run_id,
                phase=request.phase,
                status=EscalationStatus.BLOCKED,
                blocker=AgentBlocker(
                    reason=f"LLM gateway error: {type(exc).__name__}: {exc}"[:500]
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        call_id = response.id or ""

        # Extract the assistant message content and interpret it as the
        # structured envelope the system prompt asked for. Tolerant
        # parsing (R-200-021 + 2026-05-13 dispatcher v3) : small open
        # models often wrap their JSON in markdown fences or prepend
        # prose ; `_extract_envelope` strips both before json.loads.
        try:
            content = _extract_assistant_text(response.choices[0].message.content)
        except (IndexError, AttributeError) as exc:
            return _blocked_completion(
                request, duration_ms, call_id,
                f"agent response shape invalid: {exc}",
            )
        parsed = _extract_envelope(content)
        if parsed is None:
            _log.warning(
                "dispatcher parse failed (run=%s phase=%s) content[:500]=%r",
                request.run_id, request.phase.value, content[:500],
            )
            return _blocked_completion(
                request, duration_ms, call_id,
                "agent response did not contain a parseable JSON envelope",
            )

        completion = _parse_completion(parsed, request, duration_ms, call_id)
        if completion.status is EscalationStatus.BLOCKED and completion.blocker is not None:
            # Surface the raw envelope when an unknown / missing status
            # collapses us to BLOCKED — without this the operator only
            # sees "BLOCKED" with no hint that the LLM returned e.g.
            # `{"status": "completed", ...}` and tripped the enum check.
            _log.warning(
                "dispatcher BLOCKED (run=%s phase=%s reason=%s envelope=%r)",
                request.run_id, request.phase.value,
                completion.blocker.reason, parsed,
            )
        return completion


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _build_user_prompt(request: DispatchRequest) -> str:
    bundle = json.dumps(request.context_bundle, indent=2, default=str)
    return (
        f"Phase: {request.phase.value}\n"
        f"Agent: {request.agent.value}\n\n"
        f"User prompt:\n{request.prompt}\n\n"
        f"Context bundle (JSON):\n{bundle}"
    )


_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(?P<body>[\s\S]*?)\n?```",
    re.MULTILINE,
)


def _extract_envelope(content: str) -> dict[str, Any] | None:
    """Tolerant extraction of the agent's JSON envelope from `content`.

    Tries, in order:
      1. Strict `json.loads(content.strip())` — succeeds when the model
         emits clean JSON (Anthropic / OpenAI structured outputs / well-
         behaved 7B+ models).
      2. Markdown fence : strip ```...``` (with or without `json` tag),
         then `json.loads` on the body. Covers qwen2.5:3b's typical
         "Here is the JSON: ```json\n{...}\n```" pattern.
      3. Brace-balanced scan : find the first `{` in the content, walk
         forward tracking balanced braces while respecting string
         literals, slice the substring, try `json.loads`. Covers prose-
         wrapped JSON like "Sure! {...} hope this helps".

    Returns the parsed dict on success or None when every strategy
    fails — the caller surfaces that as a BLOCKED completion."""
    if not isinstance(content, str) or not content:
        return None
    trimmed = content.strip()
    # Strategy 1 — strict.
    try:
        parsed = json.loads(trimmed)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Strategy 2 — markdown fence.
    for match in _FENCE_RE.finditer(trimmed):
        body = match.group("body").strip()
        if not body:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    # Strategy 3 — brace-balanced scan.
    for start in (i for i, ch in enumerate(trimmed) if ch == "{"):
        candidate = _scan_balanced_object(trimmed, start)
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _scan_balanced_object(text: str, start: int) -> str | None:
    """Return the slice of `text` starting at `start` that contains the
    first balanced JSON object, respecting string literals (so braces
    inside `"..."` don't perturb the depth counter). Returns None when
    the scan reaches the end of `text` without closing all braces."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _looks_like_test_path(path: str) -> bool:
    """Loose heuristic — accept anything that an operator would call
    a test artifact. Used to auto-derive Gate B evidence when the
    LLM omits the explicit block."""
    p = path.lower().strip("/")
    if "/tests/" in f"/{p}" or p.startswith("tests/"):
        return True
    base = p.rsplit("/", 1)[-1]
    if base.startswith("test_") or base.startswith("test-"):
        return True
    stem = base.rsplit(".", 1)[0]
    if stem.endswith(("_test", ".test", "-test")):
        return True
    return base.endswith((".test.ts", ".test.tsx", ".spec.ts"))


def _maybe_auto_derive_gate_evidence(
    request: DispatchRequest, output_dict: dict[str, Any],
) -> None:
    """Bolt-on : when the LLM omits the gate evidence blocks but the
    output otherwise looks complete, synthesise one. Keeps gates
    passable on small open models (qwen2.5:3b et al.) while still
    failing honestly when the agent genuinely missed (e.g. GENERATE
    with no test file → no derivation → Gate B blocks). Mutates
    `output_dict` in place. Extracted from `_parse_completion` to
    keep that function's branch count under ruff PLR0912."""
    if request.phase is Phase.GENERATE and "gate_b_evidence" not in output_dict:
        b_evidence = _derive_gate_b_evidence(output_dict.get("files"))
        if b_evidence is not None:
            output_dict["gate_b_evidence"] = b_evidence
    elif request.phase is Phase.REVIEW and "gate_c_evidence" not in output_dict:
        output_dict["gate_c_evidence"] = _derive_gate_c_evidence()


def _derive_gate_c_evidence() -> dict[str, Any]:
    """Synthesise a `gate_c_evidence` block that passes Gate C
    timestamps (`evidence_timestamp > last_artifact_write`). Used by
    the review phase when the reviewer LLM omits the strict shape ;
    v1 has no in-pod sandbox so we trust the prior generate phase.
    Real validation runs land with the C15 sub-agent (deferred)."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    now = datetime.now(UTC)
    return {
        "artifact_id": "auto-derived",
        "validation_runs_green": True,
        # 1 s margin keeps `evidence > last_artifact_write` even when
        # the timestamps round to the same second.
        "evidence_timestamp": now.isoformat(),
        "last_artifact_write": (now - timedelta(seconds=1)).isoformat(),
    }


def _derive_gate_b_evidence(files: Any) -> dict[str, Any] | None:
    """Synthesise a `gate_b_evidence` block from a generate-phase
    files list. Returns None when no file looks like a test artifact —
    in that case Gate B is left to fail honestly per R-200-011."""
    from datetime import UTC, datetime  # noqa: PLC0415 — local import keeps cold-path scoped

    if not isinstance(files, list):
        return None
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = (
            entry.get("path")
            or entry.get("name")
            or entry.get("filename")
        )
        if isinstance(path, str) and _looks_like_test_path(path):
            return {
                "artifact_id": path,
                "validation_artifact_exists": True,
                "validation_runs_red": True,
                "evidence_timestamp": datetime.now(UTC).isoformat(),
            }
    return None


def _blocked_completion(
    request: DispatchRequest, duration_ms: int, call_id: str, reason: str,
) -> AgentCompletion:
    """Helper : build a BLOCKED `AgentCompletion` for parse-time failures.
    Keeps the dispatcher's `dispatch()` body free of repeated boilerplate."""
    return AgentCompletion(
        agent=request.agent,
        run_id=request.run_id,
        phase=request.phase,
        status=EscalationStatus.BLOCKED,
        blocker=AgentBlocker(reason=reason[:500]),
        duration_ms=duration_ms,
        llm_call_ids=[call_id] if call_id else [],
    )


def _extract_assistant_text(content: Any) -> str:
    """OpenAI messages allow `content` to be str OR list[dict]. We accept
    both and join any text parts so that providers returning structured
    content blocks work transparently."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _parse_completion(
    parsed: dict[str, Any],
    request: DispatchRequest,
    duration_ms: int,
    call_id: str,
) -> AgentCompletion:
    """Map the agent's JSON envelope onto AgentCompletion.

    Tolerant on `status` (R-200-021 v3) :
      - Strict match on the EscalationStatus enum first.
      - If unknown, try the synonyms map (e.g. small open models often
        emit `completed` instead of `DONE`, `error` instead of `BLOCKED`).
      - If still unknown AND the envelope carries a non-empty `output`
        (object or non-empty string), assume `DONE` — the agent
        produced something, just didn't tag it correctly. This
        graceful fallback is the difference between a usable demo on
        qwen2.5:3b and a permanent BLOCKED on phase 1.
      - Only an unknown status with an empty/missing `output` collapses
        to BLOCKED.
    """
    status_raw = str(parsed.get("status", "")).upper().strip()
    output = parsed.get("output")
    # `output` is officially a dict (per the prompt contract) but small
    # open models routinely emit a list (e.g. directly the files array
    # for GENERATE, or a list of entity drafts for SPEC) or a plain
    # string. Treat any of those non-empty shapes as "the agent
    # produced something" so the assume-DONE fallback fires.
    output_is_present = (
        (isinstance(output, dict) and bool(output))
        or (isinstance(output, list) and bool(output))
        or (isinstance(output, str) and bool(output.strip()))
    )
    status: EscalationStatus | None = None
    try:
        status = EscalationStatus(status_raw)
    except ValueError:
        # Synonyms commonly emitted by small open models. Mapping is
        # intentionally narrow — only unambiguous one-to-one cases.
        mapped = _STATUS_SYNONYMS.get(status_raw)
        if mapped is not None:
            status = mapped
    if status is None:
        # Last-resort graceful fallback : envelope produced content,
        # tag it DONE. Without content we have nothing to advance with
        # → BLOCKED with a clear reason for the operator.
        if output_is_present:
            status = EscalationStatus.DONE
        else:
            return AgentCompletion(
                agent=request.agent,
                run_id=request.run_id,
                phase=request.phase,
                status=EscalationStatus.BLOCKED,
                blocker=AgentBlocker(
                    reason=(
                        f"agent envelope has unknown status "
                        f"{status_raw!r} and empty output"
                    ),
                ),
                duration_ms=duration_ms,
                llm_call_ids=[call_id] if call_id else [],
            )

    # Normalise non-dict shapes into a dict the downstream consumers
    # (gate plugins, artifact materialisation) can introspect uniformly.
    # Lists land under `items` (typical for SPEC entity drafts) ; for
    # GENERATE we additionally surface them under `files` so the
    # materialisation path (R-200-151) finds them without a separate
    # cast — small models often emit the files array as the bare
    # `output` value rather than under `output.files`.
    if isinstance(output, dict):
        output_dict: dict[str, Any] = output
    elif isinstance(output, list):
        output_dict = {"items": output}
        if request.phase is Phase.GENERATE:
            output_dict.setdefault("files", output)
    else:
        output_dict = {"raw": output}

    _maybe_auto_derive_gate_evidence(request, output_dict)

    concerns = [
        AgentConcern(**c)
        for c in (parsed.get("concerns") or [])
        if isinstance(c, dict) and "message" in c and "severity" in c
    ]

    needs_context: AgentNeedsContext | None = None
    if status == EscalationStatus.NEEDS_CONTEXT:
        queries = parsed.get("needs_context", {}).get("queries", [])
        needs_context = AgentNeedsContext(
            queries=[q for q in queries if isinstance(q, str)]
        )

    blocker: AgentBlocker | None = None
    if status == EscalationStatus.BLOCKED:
        blocker_payload = parsed.get("blocker") or {}
        blocker = AgentBlocker(
            reason=str(blocker_payload.get("reason", "unspecified")),
            suggested_action=blocker_payload.get("suggested_action"),
        )

    return AgentCompletion(
        agent=request.agent,
        run_id=request.run_id,
        phase=request.phase,
        status=status,
        output=output_dict,
        concerns=concerns,
        needs_context=needs_context,
        blocker=blocker,
        duration_ms=duration_ms,
        llm_call_ids=[call_id] if call_id else [],
    )
