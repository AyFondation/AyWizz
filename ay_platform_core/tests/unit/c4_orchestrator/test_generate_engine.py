# =============================================================================
# File: test_generate_engine.py
# Version: 2
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_generate_engine.py
# Description: Unit tests for the V2 #2 `generate`-phase engine seam :
#                - `build_generate_engine` factory (flag → engine | None) ;
#                - `OpenHandsGenerateEngine` adapter mapping (Q13 POC) :
#                    FINISHED → DONE + output.files ; ERROR/STUCK → BLOCKED ;
#                    missing extra → BLOCKED ; any runner error → loud BLOCKED ;
#                  with the OpenHands SDK mocked at the RUNNER boundary (the
#                  dependency), never the engine under test (CLAUDE.md §10.2) ;
#                - the OrchestratorService seam : the GENERATE phase routes
#                  through the engine WHEN wired, every other phase (and the
#                  default no-engine config) uses the dispatcher unchanged.
#              No I/O against OpenHands — the fake runner writes a real temp
#              workspace so the engine's file-collection runs for real.
#
#              v2 (2026-05-22) : the OpenHands engine became the real Q13 POC
#              adapter (was an always-BLOCKED stub in v1). Test contract
#              updated accordingly (§10.4 case D) : the v1 "POC stub" assertion
#              is replaced by adapter-mapping assertions.
# =============================================================================

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.dispatcher.in_process import agent_for_phase
from ay_platform_core.c4_orchestrator.events.null_publisher import NullPublisher
from ay_platform_core.c4_orchestrator.models import (
    AgentCompletion,
    EscalationStatus,
    Phase,
)
from ay_platform_core.c4_orchestrator.pipeline.generate_engine import (
    OpenHandsEngineConfig,
    OpenHandsGenerateEngine,
    OpenHandsUnavailableError,
    _RunOutcome,
    build_generate_engine,
)
from ay_platform_core.c4_orchestrator.service import OrchestratorService

pytestmark = pytest.mark.unit


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_key": "run-1",
        "run_id": "run-1",
        "project_id": "p1",
        "session_id": "s1",
        "tenant_id": "t1",
        "user_id": "u1",
        "domain": "code",
        "initial_prompt": "build the thing",
        "current_phase": Phase.GENERATE.value,
        "concerns": [],
        "trace": [],
        "pending_steer": [],
    }
    base.update(overrides)
    return base


def _request(**overrides: Any) -> DispatchRequest:
    base: dict[str, Any] = {
        "run_id": "r1",
        "phase": Phase.GENERATE,
        "agent": agent_for_phase(Phase.GENERATE),
        "session_id": "s",
        "tenant_id": "t",
        "user_id": "u",
        "project_id": "p",
        "prompt": "x",
        "context_bundle": {},
    }
    base.update(overrides)
    return DispatchRequest(**base)


def _completion(request: DispatchRequest, status: EscalationStatus) -> AgentCompletion:
    return AgentCompletion(
        agent=request.agent, run_id=request.run_id, phase=request.phase, status=status,
    )


def _config() -> OpenHandsEngineConfig:
    return OpenHandsEngineConfig(
        gateway_url="http://c8:8000/v1",
        api_key="k",
        model="litellm_proxy/claude-opus-flagship",
    )


class _FakeRunner:
    """Stands in for `_default_runner` — i.e. the OpenHands DEPENDENCY, not
    the engine under test. Writes a real temp workspace on the success path
    so the engine's file-collection + cleanup run for real."""

    def __init__(
        self,
        *,
        status: str = "FINISHED",
        files: dict[str, str] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.status = status
        self.files = files or {}
        self.raises = raises
        self.workspace: Path | None = None
        self.calls: list[DispatchRequest] = []

    def __call__(
        self, config: OpenHandsEngineConfig, request: DispatchRequest
    ) -> _RunOutcome:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        workspace = Path(tempfile.mkdtemp(prefix="fake-oh-"))
        self.workspace = workspace
        for rel, content in self.files.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return _RunOutcome(workspace=workspace, execution_status=self.status)


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[DispatchRequest] = []

    async def dispatch(self, request: DispatchRequest) -> AgentCompletion:
        self.calls.append(request)
        return _completion(request, EscalationStatus.DONE)


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[DispatchRequest] = []

    async def invoke(self, request: DispatchRequest) -> AgentCompletion:
        self.calls.append(request)
        return _completion(request, EscalationStatus.BLOCKED)


class _FakeRepo:
    async def upsert_run(self, row: dict[str, Any]) -> None:
        return None


def _svc(
    *, dispatcher: _FakeDispatcher, engine: _FakeEngine | None,
) -> OrchestratorService:
    return OrchestratorService(
        config=OrchestratorConfig(),
        repo=_FakeRepo(),  # type: ignore[arg-type]
        dispatcher=dispatcher,
        domain_plugin=None,  # type: ignore[arg-type]  # unused by _invoke_agent
        publisher=NullPublisher(),
        generate_engine=engine,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_openhands_flag_builds_the_engine(self) -> None:
        assert isinstance(build_generate_engine("openhands"), OpenHandsGenerateEngine)

    def test_openhands_flag_with_config_builds_the_engine(self) -> None:
        engine = build_generate_engine("openhands", _config())
        assert isinstance(engine, OpenHandsGenerateEngine)

    def test_in_process_flag_returns_none(self) -> None:
        # None → the orchestrator keeps the dispatcher path (unchanged).
        assert build_generate_engine("in_process") is None

    def test_unknown_flag_falls_back_to_none(self) -> None:
        assert build_generate_engine("bogus") is None


# ---------------------------------------------------------------------------
# OpenHands adapter mapping (runner mocked)
# ---------------------------------------------------------------------------


class TestOpenHandsAdapter:
    async def test_finished_returns_done_with_collected_files(self) -> None:
        runner = _FakeRunner(
            status="FINISHED",
            files={
                "src/app.py": "print('hi')\n",
                "tests/test_app.py": "def test_x(): assert True\n",
                ".git/config": "[core]\n",  # runtime internal — must be skipped
            },
        )
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request())

        assert completion.status is EscalationStatus.DONE
        assert completion.output["engine"] == "openhands"
        paths = {f["path"] for f in completion.output["files"]}
        assert paths == {"src/app.py", "tests/test_app.py"}  # .git excluded
        contents = {f["path"]: f["content"] for f in completion.output["files"]}
        assert contents["src/app.py"] == "print('hi')\n"
        # POC (Q1) : NO gate_b_evidence is fabricated.
        assert "gate_b_evidence" not in completion.output
        # The temp workspace is cleaned up after reading.
        assert runner.workspace is not None
        assert not runner.workspace.exists()

    async def test_finished_with_empty_workspace_is_done_with_no_files(self) -> None:
        runner = _FakeRunner(status="FINISHED", files={})
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request())

        assert completion.status is EscalationStatus.DONE
        assert completion.output["files"] == []

    async def test_error_status_returns_blocked(self) -> None:
        runner = _FakeRunner(status="ERROR")
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request())

        assert completion.status is EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "ERROR" in completion.blocker.reason
        assert runner.workspace is not None
        assert not runner.workspace.exists()  # cleaned up even on non-FINISHED

    async def test_stuck_status_returns_blocked(self) -> None:
        runner = _FakeRunner(status="STUCK")
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request())

        assert completion.status is EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "STUCK" in completion.blocker.reason

    async def test_missing_extra_returns_blocked_with_install_hint(self) -> None:
        runner = _FakeRunner(raises=OpenHandsUnavailableError("No module named 'openhands'"))
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request())

        assert completion.status is EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "not installed" in completion.blocker.reason
        assert completion.blocker.suggested_action is not None
        assert "in_process" in completion.blocker.suggested_action

    async def test_runner_runtime_error_is_swallowed_into_loud_blocked(self) -> None:
        # A gated engine MUST NOT crash the orchestrator : any SDK/runtime
        # error becomes a BLOCKED completion carrying the error detail.
        runner = _FakeRunner(raises=RuntimeError("boom"))
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request())

        assert completion.status is EscalationStatus.BLOCKED
        assert completion.blocker is not None
        assert "boom" in completion.blocker.reason

    async def test_request_envelope_preserved_on_completion(self) -> None:
        runner = _FakeRunner(status="FINISHED")
        engine = OpenHandsGenerateEngine(_config(), runner=runner)

        completion = await engine.invoke(_request(run_id="r-42"))

        assert completion.run_id == "r-42"
        assert completion.phase is Phase.GENERATE
        assert completion.agent == agent_for_phase(Phase.GENERATE)


# ---------------------------------------------------------------------------
# OrchestratorService seam
# ---------------------------------------------------------------------------


class TestServiceSeam:
    async def test_generate_routes_through_engine_when_wired(self) -> None:
        dispatcher, engine = _FakeDispatcher(), _FakeEngine()
        svc = _svc(dispatcher=dispatcher, engine=engine)
        completion = await svc._invoke_agent(_row(), Phase.GENERATE)
        assert len(engine.calls) == 1
        assert dispatcher.calls == []
        assert completion.status is EscalationStatus.BLOCKED  # the engine's result

    async def test_non_generate_phase_always_uses_dispatcher(self) -> None:
        dispatcher, engine = _FakeDispatcher(), _FakeEngine()
        svc = _svc(dispatcher=dispatcher, engine=engine)
        await svc._invoke_agent(_row(current_phase=Phase.PLAN.value), Phase.PLAN)
        assert len(dispatcher.calls) == 1
        assert engine.calls == []

    async def test_generate_uses_dispatcher_when_no_engine(self) -> None:
        dispatcher = _FakeDispatcher()
        svc = _svc(dispatcher=dispatcher, engine=None)
        completion = await svc._invoke_agent(_row(), Phase.GENERATE)
        assert len(dispatcher.calls) == 1
        assert completion.status is EscalationStatus.DONE  # the dispatcher's result
