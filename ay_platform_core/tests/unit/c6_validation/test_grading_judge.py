# =============================================================================
# File: test_grading_judge.py
# Version: 1
# Path: ay_platform_core/tests/unit/c6_validation/test_grading_judge.py
# Description: Unit tests for C6 T3 LLM-as-judge grading (R-700-032). Covers
#              the pure parsing/clamping logic of `grade_judged` (with a fake
#              C8 client) AND the additive ValidationService wiring (judged
#              verdict is persisted only when judge is enabled + a client is
#              wired, and a judge failure never breaks the run).
#
# @relation validates:R-700-032
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.grading_judge import grade_judged
from ay_platform_core.c6_validation.models import (
    CodeArtifact,
    RunTriggerRequest,
    VerdictMethod,
)
from ay_platform_core.c6_validation.plugin.registry import get_registry
from ay_platform_core.c6_validation.service import ValidationService
from ay_platform_core.c8_llm.models import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatMessage,
    ChatRole,
    UsageInfo,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _resp(content: str | None) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="r-1",
        created=0,
        model="judge-model",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role=ChatRole.ASSISTANT, content=content),
            )
        ],
        usage=UsageInfo(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class _FakeLLM:
    """Minimal stand-in for LLMGatewayClient. Records the call kwargs and
    either returns a canned response or raises."""

    def __init__(
        self, *, reply: ChatCompletionResponse | None = None, exc: Exception | None = None
    ) -> None:
        self._reply = reply
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(
        self, payload: Any, *, agent_name: str, session_id: str, **kwargs: Any
    ) -> ChatCompletionResponse:
        self.calls.append(
            {"agent_name": agent_name, "session_id": session_id, **kwargs}
        )
        if self._exc is not None:
            raise self._exc
        assert self._reply is not None
        return self._reply


_REQS = [{"entity_id": "R-100-001", "title": "do a thing"}]
_ARTS = [CodeArtifact(path="src/x.py", content="# @relation implements:R-100-001\n")]


async def _grade(llm: Any) -> Any:
    return await grade_judged(
        llm_client=llm,
        run_id="run-1",
        domain="code",
        requirements=_REQS,
        artifacts=_ARTS,
        agent_name="c6-judge-test",
        project_id="demo",
    )


# ---------------------------------------------------------------------------
# grade_judged — happy path + provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_json_maps_to_judged_verdict() -> None:
    llm = _FakeLLM(
        reply=_resp(
            '{"score": 0.8, "confidence": 0.6, "rationale": "decent", '
            '"evidence": ["src/x.py", "R-100-001"]}'
        )
    )
    verdict = await _grade(llm)
    assert verdict is not None
    assert verdict.method is VerdictMethod.JUDGED
    assert verdict.score == 0.8
    assert verdict.confidence == 0.6
    assert verdict.rationale == "decent"
    assert verdict.evidence == ["src/x.py", "R-100-001"]
    assert verdict.run_id == "run-1"
    assert verdict.domain == "code"


@pytest.mark.asyncio
async def test_routes_through_the_configured_judge_agent() -> None:
    llm = _FakeLLM(reply=_resp('{"score": 1.0, "confidence": 0.5, "rationale": "ok"}'))
    await _grade(llm)
    assert llm.calls[0]["agent_name"] == "c6-judge-test"


# ---------------------------------------------------------------------------
# grade_judged — robustness (clamping, lenient parse, best-effort)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_out_of_range_values_are_clamped() -> None:
    # score 1.5 → 1.0 ; confidence 2.0 → capped at 0.99 (never 1.0 for a judge).
    llm = _FakeLLM(reply=_resp('{"score": 1.5, "confidence": 2.0, "rationale": "x"}'))
    verdict = await _grade(llm)
    assert verdict is not None
    assert verdict.score == 1.0
    assert verdict.confidence == 0.99


@pytest.mark.asyncio
async def test_negative_score_clamped_to_zero() -> None:
    llm = _FakeLLM(reply=_resp('{"score": -3, "confidence": 0.4, "rationale": "x"}'))
    verdict = await _grade(llm)
    assert verdict is not None
    assert verdict.score == 0.0


@pytest.mark.asyncio
async def test_json_wrapped_in_prose_is_extracted() -> None:
    llm = _FakeLLM(
        reply=_resp(
            'Sure! Here is my grade:\n```json\n'
            '{"score": 0.5, "confidence": 0.5, "rationale": "mid"}\n```\nThanks.'
        )
    )
    verdict = await _grade(llm)
    assert verdict is not None
    assert verdict.score == 0.5


@pytest.mark.asyncio
async def test_missing_optional_fields_use_defaults() -> None:
    # No confidence / rationale / evidence in the reply.
    llm = _FakeLLM(reply=_resp('{"score": 0.7}'))
    verdict = await _grade(llm)
    assert verdict is not None
    assert verdict.score == 0.7
    assert verdict.confidence == 0.5  # default
    assert verdict.evidence == []
    assert "no rationale" in verdict.rationale


@pytest.mark.asyncio
async def test_unparseable_reply_returns_none() -> None:
    llm = _FakeLLM(reply=_resp("I cannot grade this, sorry."))
    assert await _grade(llm) is None


@pytest.mark.asyncio
async def test_null_content_returns_none() -> None:
    llm = _FakeLLM(reply=_resp(None))
    assert await _grade(llm) is None


@pytest.mark.asyncio
async def test_provider_error_returns_none() -> None:
    llm = _FakeLLM(exc=RuntimeError("provider exploded"))
    assert await _grade(llm) is None


# ---------------------------------------------------------------------------
# ValidationService wiring (additive, opt-in)
# ---------------------------------------------------------------------------


class _MemRepo:
    """In-memory ValidationRepository fake covering the sync-exec path."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.findings: list[dict[str, Any]] = []

    async def upsert_run(self, row: dict[str, Any]) -> None:
        self.runs[row["run_id"]] = row

    async def insert_findings(self, rows: list[dict[str, Any]]) -> None:
        self.findings.extend(rows)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)


def _service(*, judge_enabled: bool, llm: Any) -> ValidationService:
    cfg = ValidationConfig(judge_enabled=judge_enabled)
    return ValidationService(
        config=cfg,
        registry=get_registry(),
        repo=_MemRepo(),  # type: ignore[arg-type]
        snapshot_store=None,
        llm_client=llm,
    )


async def _run(svc: ValidationService) -> Any:
    payload = RunTriggerRequest(
        domain="code",
        project_id="demo",
        check_ids=["interface-signature-drift"],  # deterministic 1 info finding
    )
    return await svc.execute_run_sync(payload, requirements=[], artifacts=[])


@pytest.mark.asyncio
async def test_service_persists_judged_verdict_when_enabled() -> None:
    llm = _FakeLLM(reply=_resp('{"score": 0.9, "confidence": 0.7, "rationale": "good"}'))
    run = await _run(_service(judge_enabled=True, llm=llm))
    assert run.judged_verdict is not None
    assert run.judged_verdict.method is VerdictMethod.JUDGED
    assert run.judged_verdict.score == 0.9
    # T1 verdict is still present and untouched.
    assert run.verdict is not None
    assert run.verdict.method is VerdictMethod.DETERMINISTIC


@pytest.mark.asyncio
async def test_service_skips_judge_when_disabled() -> None:
    llm = _FakeLLM(reply=_resp('{"score": 0.9, "confidence": 0.7, "rationale": "good"}'))
    run = await _run(_service(judge_enabled=False, llm=llm))
    assert run.judged_verdict is None
    assert llm.calls == []  # judge was never called


@pytest.mark.asyncio
async def test_service_skips_judge_when_no_client() -> None:
    run = await _run(_service(judge_enabled=True, llm=None))
    assert run.judged_verdict is None


@pytest.mark.asyncio
async def test_judge_failure_does_not_break_the_run() -> None:
    llm = _FakeLLM(exc=RuntimeError("boom"))
    run = await _run(_service(judge_enabled=True, llm=llm))
    # Run still completes with a T1 verdict ; only the judged grade is absent.
    assert run.status.value == "completed"
    assert run.verdict is not None
    assert run.judged_verdict is None
