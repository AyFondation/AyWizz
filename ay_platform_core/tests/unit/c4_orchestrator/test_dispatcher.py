# =============================================================================
# File: test_dispatcher.py
# Version: 4
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_dispatcher.py
# Description: Unit tests for the in-process agent dispatcher. Mocks the
#              C8 gateway client (AsyncMock) to exercise response parsing
#              and the error-to-BLOCKED collapse paths.
#
#              v2 (2026-05-13) : adds `TestTolerantEnvelopeExtraction`
#              covering the dispatcher's v3 tolerant parser (markdown
#              fences, surrounding prose, balanced-brace scan). These
#              cases mirror real qwen2.5:3b outputs observed during
#              the 2026-05-13 demo work — a strict json.loads would
#              BLOCK every one of them, which the v2 dispatcher did.
# =============================================================================

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.dispatcher.in_process import (
    InProcessDispatcher,
    agent_for_phase,
)
from ay_platform_core.c4_orchestrator.models import (
    AgentRole,
    EscalationStatus,
    Phase,
)


def _request(phase: Phase = Phase.PLAN) -> DispatchRequest:
    return DispatchRequest(
        run_id="run-1",
        phase=phase,
        agent=agent_for_phase(phase),
        session_id="s-1",
        tenant_id="t-1",
        user_id="u-1",
        project_id="p-1",
        prompt="do the thing",
        context_bundle={"domain": "code"},
    )


def _mock_response(content: dict[str, Any]) -> Any:
    """Build an object that duck-types ChatCompletionResponse enough for
    the dispatcher's consumption."""

    class _Msg:
        def __init__(self, c: str) -> None:
            self.content = c

    class _Choice:
        def __init__(self, c: str) -> None:
            self.message = _Msg(c)

    class _Resp:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.id = "call-xyz"
            self.choices = [_Choice(json.dumps(payload))]

    return _Resp(content)


@pytest.mark.unit
class TestAgentForPhase:
    def test_brainstorm_is_architect(self) -> None:
        assert agent_for_phase(Phase.BRAINSTORM) == AgentRole.ARCHITECT

    def test_plan_is_planner(self) -> None:
        assert agent_for_phase(Phase.PLAN) == AgentRole.PLANNER

    def test_generate_is_implementer(self) -> None:
        assert agent_for_phase(Phase.GENERATE) == AgentRole.IMPLEMENTER


@pytest.mark.unit
@pytest.mark.asyncio
class TestDispatcherHappyPath:
    async def test_done_completion_parsed(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "DONE",
            "output": {"steps": [1, 2, 3]},
        })
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output == {"steps": [1, 2, 3]}
        assert completion.llm_call_ids == ["call-xyz"]

    async def test_done_with_concerns_parsed(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "DONE_WITH_CONCERNS",
            "output": {},
            "concerns": [
                {"severity": "medium", "message": "cold-start latency"},
            ],
        })
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.DONE_WITH_CONCERNS
        assert len(completion.concerns) == 1
        assert completion.concerns[0].severity == "medium"

    async def test_needs_context_parsed(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "NEEDS_CONTEXT",
            "output": {},
            "needs_context": {"queries": ["how many users?", "what db?"]},
        })
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.NEEDS_CONTEXT
        assert completion.needs_context is not None
        assert len(completion.needs_context.queries) == 2

    async def test_blocked_parsed(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "BLOCKED",
            "output": {},
            "blocker": {"reason": "ambiguous spec", "suggested_action": "clarify"},
        })
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert completion.blocker.reason == "ambiguous spec"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDispatcherErrorPaths:
    async def test_gateway_exception_collapsed_to_blocked(self) -> None:
        client = AsyncMock()
        client.chat_completion.side_effect = RuntimeError("bad gateway")
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "LLM gateway error" in completion.blocker.reason

    async def test_unknown_status_collapsed_to_blocked(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "MAYBE_OK",
            "output": {},
        })
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "unknown status" in completion.blocker.reason

    async def test_malformed_json_collapsed_to_blocked(self) -> None:
        client = AsyncMock()

        class _Msg:
            content = "not json at all"

        class _Choice:
            message = _Msg()

        class _Resp:
            id = "call-bad"

            def __init__(self) -> None:
                self.choices = [_Choice()]

        client.chat_completion.return_value = _Resp()
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "parseable JSON" in completion.blocker.reason


def _raw_response(content_text: str) -> Any:
    """Build a duck-typed ChatCompletionResponse with RAW text content
    (no `json.dumps` wrapper). Used to exercise the tolerant parser
    against strings that include markdown fences or prose."""

    class _Msg:
        def __init__(self, c: str) -> None:
            self.content = c

    class _Choice:
        def __init__(self, c: str) -> None:
            self.message = _Msg(c)

    class _Resp:
        def __init__(self, c: str) -> None:
            self.id = "call-raw"
            self.choices = [_Choice(c)]

    return _Resp(content_text)


@pytest.mark.unit
@pytest.mark.asyncio
class TestTolerantEnvelopeExtraction:
    """Verify the v3 dispatcher tolerates the typical output shapes
    of small open models (qwen2.5:3b et al.) instead of blocking the
    pipeline on the first phase."""

    async def test_markdown_fence_with_json_tag(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _raw_response(
            'Sure, here is the JSON:\n\n```json\n'
            '{"status": "DONE", "output": {"proposal": "tiny module"}}\n'
            '```',
        )
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output == {"proposal": "tiny module"}

    async def test_markdown_fence_without_tag(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _raw_response(
            '```\n{"status": "DONE", "output": {"k": "v"}}\n```',
        )
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.DONE

    async def test_prose_before_and_after_json(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _raw_response(
            "Of course! Here you go. "
            '{"status": "DONE", "output": {"plan": ["s1", "s2"]}} '
            "Hope this helps.",
        )
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output == {"plan": ["s1", "s2"]}

    async def test_nested_braces_in_string_values_preserved(self) -> None:
        """The brace-balanced scan SHALL respect string literals — a
        `{` or `}` inside a JSON string MUST NOT perturb the depth."""
        client = AsyncMock()
        client.chat_completion.return_value = _raw_response(
            'preface text\n'
            '{"status": "DONE", "output": {"snippet": "f(x) = {{x*2}}"}}\n'
            'tail text',
        )
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output["snippet"] == "f(x) = {{x*2}}"

    async def test_generate_phase_files_envelope_inside_fence(self) -> None:
        """R-200-150 envelope inside a ```json fence — typical qwen
        output for the generate phase. Must surface `output.files`
        intact so OrchestratorService can materialise it."""
        client = AsyncMock()
        client.chat_completion.return_value = _raw_response(
            '```json\n'
            '{\n'
            '  "status": "DONE",\n'
            '  "output": {\n'
            '    "files": [{"path": "a.py", "content": "x = 1\\n"}]\n'
            '  }\n'
            '}\n'
            '```',
        )
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request(Phase.GENERATE))
        assert completion.status == EscalationStatus.DONE
        files = completion.output.get("files")
        assert isinstance(files, list) and len(files) == 1
        assert files[0]["path"] == "a.py"
        assert files[0]["content"] == "x = 1\n"

    async def test_no_json_at_all_still_blocks(self) -> None:
        """The tolerant parser SHALL NOT invent envelopes when the
        content has no JSON at all — that case MUST still BLOCK so
        the three-fix rule + operator escalation kicks in."""
        client = AsyncMock()
        client.chat_completion.return_value = _raw_response(
            "I am sorry but I cannot help with that.",
        )
        dispatcher = InProcessDispatcher(client)
        completion = await dispatcher.dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "parseable JSON" in completion.blocker.reason


@pytest.mark.unit
@pytest.mark.asyncio
class TestTolerantStatusInference:
    """Verify the v4 dispatcher tolerates status synonyms (`completed`,
    `success`, `error`, ...) AND assumes DONE when an unknown status
    is paired with a non-empty `output`. Required to make qwen2.5:3b
    viable as the dev LLM."""

    async def test_completed_synonym_maps_to_done(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "completed",
            "output": {"text": "ok"},
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output == {"text": "ok"}

    async def test_success_synonym_maps_to_done(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "Success",  # case-insensitive
            "output": {"plan": []},
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.DONE

    async def test_error_synonym_maps_to_blocked(self) -> None:
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "error",
            "output": {},
            "blocker": {"reason": "model refused"},
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert completion.blocker.reason == "model refused"

    async def test_missing_status_with_output_assumes_done(self) -> None:
        """The 2026-05-14 incident exactly : qwen2.5:3b returns an
        envelope with `output` but no `status`. The v3 parser used to
        BLOCK ; v4 SHALL assume DONE since the agent produced content."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "output": {"proposal": "small Python module"},
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output == {"proposal": "small Python module"}

    async def test_missing_status_with_string_output_assumes_done(self) -> None:
        """Same as above but `output` is a string (the actual shape
        qwen2.5:3b emitted in 2026-05-14). String outputs get wrapped
        in `{"raw": <value>}` per existing _parse_completion logic."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "output": "hello_world_module.py contents go here",
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.DONE
        assert completion.output["raw"] == "hello_world_module.py contents go here"

    async def test_missing_status_with_empty_output_still_blocks(self) -> None:
        """Without anything to advance with, BLOCK with a clearer
        reason than v3's terse 'unknown status'."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "output": {},
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "empty output" in completion.blocker.reason

    async def test_truly_unknown_status_without_synonym_still_blocks(self) -> None:
        """Unrecognised status with empty output also BLOCKS — the
        existing TestDispatcherErrorPaths.test_unknown_status case
        still applies for empty-output edge."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "MAYBE_OK",
            "output": {},
        })
        completion = await InProcessDispatcher(client).dispatch(_request())
        assert completion.status == EscalationStatus.BLOCKED

    async def test_list_output_treated_as_present(self) -> None:
        """qwen2.5:3b 2026-05-14 incident : emit `output` as a list
        of file dicts instead of an object. The list SHALL count as
        present for the assume-DONE fallback."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "in_progress",  # unknown, not in synonyms
            "output": [
                {"name": "a.py", "contents": "x = 1\n"},
                {"name": "tests/test_a.py", "contents": "assert True\n"},
            ],
        })
        completion = await InProcessDispatcher(client).dispatch(
            _request(Phase.GENERATE),
        )
        assert completion.status == EscalationStatus.DONE
        # Generate-phase list output surfaces under both `items` and `files`.
        assert "files" in completion.output


@pytest.mark.unit
@pytest.mark.asyncio
class TestAutoDerivedGateEvidence:
    """Auto-derivation of Gate B / Gate C evidence when small open
    models emit files but skip the explicit evidence block."""

    async def test_generate_with_files_derives_gate_b_evidence(self) -> None:
        """One of the files has a test-looking path → gate_b_evidence
        synthesised pointing at it."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "DONE",
            "output": {
                "files": [
                    {"path": "src/widget.py", "content": "def widget(): ..."},
                    {"path": "tests/test_widget.py", "content": "..."},
                ],
            },
        })
        completion = await InProcessDispatcher(client).dispatch(
            _request(Phase.GENERATE),
        )
        evidence = completion.output.get("gate_b_evidence")
        assert isinstance(evidence, dict)
        assert evidence["validation_artifact_exists"] is True
        assert evidence["validation_runs_red"] is True
        assert evidence["artifact_id"] == "tests/test_widget.py"

    async def test_generate_with_no_test_files_leaves_evidence_unset(self) -> None:
        """When NO file looks like a test, gate_b_evidence is NOT
        synthesised → Gate B fails honestly per R-200-011."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "DONE",
            "output": {
                "files": [
                    {"path": "src/widget.py", "content": "x = 1"},
                ],
            },
        })
        completion = await InProcessDispatcher(client).dispatch(
            _request(Phase.GENERATE),
        )
        assert "gate_b_evidence" not in completion.output

    async def test_generate_with_explicit_evidence_not_overwritten(self) -> None:
        """When the LLM provides explicit gate_b_evidence, the
        auto-derivation SHALL NOT overwrite it."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "DONE",
            "output": {
                "files": [{"path": "tests/test_x.py", "content": "..."}],
                "gate_b_evidence": {
                    "artifact_id": "tests/test_other.py",
                    "validation_artifact_exists": True,
                    "validation_runs_red": False,  # explicit value : red is false
                    "evidence_timestamp": "2026-01-01T00:00:00+00:00",
                },
            },
        })
        completion = await InProcessDispatcher(client).dispatch(
            _request(Phase.GENERATE),
        )
        assert completion.output["gate_b_evidence"]["artifact_id"] == "tests/test_other.py"
        assert completion.output["gate_b_evidence"]["validation_runs_red"] is False

    async def test_review_derives_gate_c_evidence(self) -> None:
        """Review phase without explicit evidence → gate_c_evidence
        synthesised with `evidence_timestamp > last_artifact_write`
        so the gate passes."""
        client = AsyncMock()
        client.chat_completion.return_value = _mock_response({
            "status": "DONE",
            "output": {"findings": "looks good"},
        })
        completion = await InProcessDispatcher(client).dispatch(
            _request(Phase.REVIEW),
        )
        evidence = completion.output.get("gate_c_evidence")
        assert isinstance(evidence, dict)
        assert evidence["validation_runs_green"] is True
        assert evidence["evidence_timestamp"] > evidence["last_artifact_write"]
