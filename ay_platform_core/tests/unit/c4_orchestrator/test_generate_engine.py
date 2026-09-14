# =============================================================================
# File: test_generate_engine.py
# Version: 3
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
#              v3 (2026-09-09) : covers Gate B evidence. Two new classes —
#              `TestTerminalRunExtraction` (duck-typed reading of the SDK's
#              terminal observations : nested or bare, ACTIONS skipped since
#              they record intent not result, blank commands skipped, output
#              tail-truncated, order preserved) and `TestGateBEvidence` (a
#              FAILING validation run yields evidence carrying the observed
#              command / exit code / output ; a green-only run, a test file
#              that was never executed, and an unrecognised failing command
#              all yield NO evidence so Gate B blocks). The third of those is
#              the regression guard for the defect this release fixed: a
#              test-looking FILENAME is not proof.
#
#              v2 (2026-05-22) : the OpenHands engine became the real Q13 POC
#              adapter (was an always-BLOCKED stub in v1). Test contract
#              updated accordingly (§10.4 case D) : the v1 "POC stub" assertion
#              is replaced by adapter-mapping assertions.
#
# @relation validates:R-200-011
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
    TerminalRun,
    _RunOutcome,
    _terminal_runs_from_events,
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
        terminal_runs: tuple[TerminalRun, ...] = (),
    ) -> None:
        self.status = status
        self.files = files or {}
        self.raises = raises
        self.terminal_runs = terminal_runs
        self.workspace: Path | None = None
        self.calls: list[DispatchRequest] = []
        # Per-call configs, so a test can assert WHICH model the engine
        # resolved for this run rather than which one it was constructed with.
        self.configs: list[OpenHandsEngineConfig] = []

    def __call__(
        self, config: OpenHandsEngineConfig, request: DispatchRequest
    ) -> _RunOutcome:
        self.calls.append(request)
        self.configs.append(config)
        if self.raises is not None:
            raise self.raises
        workspace = Path(tempfile.mkdtemp(prefix="fake-oh-"))
        self.workspace = workspace
        for rel, content in self.files.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return _RunOutcome(
            workspace=workspace,
            execution_status=self.status,
            terminal_runs=self.terminal_runs,
        )


class _FakeObservation:
    """Duck-typed stand-in for `TerminalObservation` — the SDK is not
    installed here, and `_terminal_runs_from_events` deliberately reads by
    attribute rather than by type."""

    def __init__(self, command: str, exit_code: int | None, text: str = "") -> None:
        self.command = command
        self.exit_code = exit_code
        self.text = text


class _FakeAction:
    """Terminal ACTION: carries a command but NO exit_code. Records intent,
    not result — must never be mistaken for evidence."""

    def __init__(self, command: str) -> None:
        self.command = command


class _FakeEvent:
    """Event wrapper — the SDK nests the observation inside the event."""

    def __init__(self, observation: object) -> None:
        self.observation = observation


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
# Model comes from the PLATFORM's LLM configuration, not from the environment
# ---------------------------------------------------------------------------


class TestModelResolution:
    """D-011 : registering + associating a model in the HMI is enough. The
    engine must honour that like every other LLM caller, instead of pinning a
    model in its own environment."""

    async def test_resolved_project_model_is_used(self) -> None:
        runner = _FakeRunner()

        async def _resolver(
            agent: str, tenant: str, project: str, *, require_tool_calling: bool = False
        ) -> str:
            return "claude-sonnet-5"

        engine = OpenHandsGenerateEngine(
            _config(), runner=runner, model_resolver=_resolver
        )
        await engine.invoke(_request())
        assert runner.configs[-1].model == "litellm_proxy/claude-sonnet-5"

    async def test_resolution_requires_tool_calling(self) -> None:
        """An agent loop that cannot call tools cannot edit a file or run a
        test — a model without that capability is broken here, not degraded."""
        seen: dict[str, Any] = {}

        async def _resolver(
            agent: str, tenant: str, project: str, *, require_tool_calling: bool = False
        ) -> str:
            seen["agent"] = agent
            seen["tenant"] = tenant
            seen["project"] = project
            seen["tool_calling"] = require_tool_calling
            return "m"

        await OpenHandsGenerateEngine(
            _config(), runner=_FakeRunner(), model_resolver=_resolver
        ).invoke(_request())
        assert seen["tool_calling"] is True
        assert seen["tenant"] and seen["project"]

    async def test_unconfigured_project_falls_back_to_the_env_value(self) -> None:
        runner = _FakeRunner()

        async def _resolver(
            agent: str, tenant: str, project: str, *, require_tool_calling: bool = False
        ) -> str | None:
            return None

        engine = OpenHandsGenerateEngine(
            _config(), runner=runner, model_resolver=_resolver
        )
        await engine.invoke(_request())
        assert runner.configs[-1].model == _config().model

    async def test_a_registry_error_never_breaks_the_run(self) -> None:
        """Best-effort: a registry outage degrades to the fallback rather than
        failing a generate phase."""
        runner = _FakeRunner()

        async def _resolver(
            agent: str, tenant: str, project: str, *, require_tool_calling: bool = False
        ) -> str:
            raise RuntimeError("arango down")

        engine = OpenHandsGenerateEngine(
            _config(), runner=runner, model_resolver=_resolver
        )
        completion = await engine.invoke(_request())
        assert completion.status is EscalationStatus.DONE
        assert runner.configs[-1].model == _config().model

    async def test_no_resolver_keeps_the_configured_model(self) -> None:
        runner = _FakeRunner()
        await OpenHandsGenerateEngine(_config(), runner=runner).invoke(_request())
        assert runner.configs[-1].model == _config().model


# ---------------------------------------------------------------------------
# Terminal-run extraction from the conversation event log
# ---------------------------------------------------------------------------


class TestTerminalRunExtraction:
    """`_terminal_runs_from_events` is duck-typed against the SDK's
    `TerminalObservation` (command / exit_code / inherited `text`)."""

    def test_reads_command_exit_code_and_output_from_nested_observation(self) -> None:
        events = [_FakeEvent(_FakeObservation("pytest -q", 1, "1 failed"))]
        assert _terminal_runs_from_events(events) == (
            TerminalRun(command="pytest -q", exit_code=1, output="1 failed"),
        )

    def test_reads_an_observation_carried_directly_on_the_event(self) -> None:
        """The SDK may surface the observation as the event itself."""
        runs = _terminal_runs_from_events([_FakeObservation("pytest", 0, "ok")])
        assert runs == (TerminalRun(command="pytest", exit_code=0, output="ok"),)

    def test_skips_actions_which_record_intent_not_result(self) -> None:
        """A terminal ACTION carries a command but no exit_code. Counting it
        would mean treating an intention to run tests as proof they ran."""
        assert _terminal_runs_from_events([_FakeAction("pytest -q")]) == ()

    def test_skips_blank_commands(self) -> None:
        """`TerminalAction.command` may be an empty string (used to poll for
        more output) — that is not a command execution."""
        assert _terminal_runs_from_events([_FakeObservation("   ", 0, "x")]) == ()

    def test_truncates_output_to_the_tail(self) -> None:
        runs = _terminal_runs_from_events([_FakeObservation("pytest", 1, "A" * 10_000)])
        assert len(runs[0].output) == 4000
        assert runs[0].output == "A" * 4000

    def test_preserves_order_of_execution(self) -> None:
        events = [
            _FakeEvent(_FakeObservation("pytest -q", 1, "red")),
            _FakeEvent(_FakeObservation("pytest -q", 0, "green")),
        ]
        assert [r.exit_code for r in _terminal_runs_from_events(events)] == [1, 0]


# ---------------------------------------------------------------------------
# Gate B evidence — emitted only from an OBSERVED failing validation run
# ---------------------------------------------------------------------------


class TestGateBEvidence:
    """R-200-011 : Gate B asserts TDD red-first. The engine emits evidence
    only where it observed a validation command that actually failed, and
    carries the proof so a reviewer can check rather than trust."""

    async def test_failing_test_run_yields_evidence_carrying_the_proof(self) -> None:
        runner = _FakeRunner(
            files={"tests/test_widget.py": "def test_x(): assert False"},
            terminal_runs=(
                TerminalRun("pytest -q", 1, "1 failed in 0.02s"),
            ),
        )
        completion = await OpenHandsGenerateEngine(
            _config(), runner=runner
        ).invoke(_request())
        evidence = completion.output["gate_b_evidence"]
        assert evidence["validation_artifact_exists"] is True
        assert evidence["validation_runs_red"] is True
        assert evidence["artifact_id"] == "tests/test_widget.py"
        # The proof itself, not just the claim.
        assert evidence["observed_command"] == "pytest -q"
        assert evidence["observed_exit_code"] == 1
        assert "1 failed" in evidence["observed_output_tail"]

    async def test_tests_that_only_ever_passed_yield_no_evidence(self) -> None:
        """Green is not red. A generate run whose tests never failed has NOT
        demonstrated red-first, so Gate B must block."""
        runner = _FakeRunner(
            files={"tests/test_widget.py": "def test_x(): assert True"},
            terminal_runs=(TerminalRun("pytest -q", 0, "1 passed"),),
        )
        completion = await OpenHandsGenerateEngine(
            _config(), runner=runner
        ).invoke(_request())
        assert "gate_b_evidence" not in completion.output

    async def test_a_test_file_without_any_run_yields_no_evidence(self) -> None:
        """The regression that motivated this release: a test-looking FILENAME
        is not proof. Producing `tests/test_widget.py` without executing it
        must not pass Gate B."""
        runner = _FakeRunner(
            files={"tests/test_widget.py": "def test_x(): assert False"},
            terminal_runs=(),
        )
        completion = await OpenHandsGenerateEngine(
            _config(), runner=runner
        ).invoke(_request())
        assert "gate_b_evidence" not in completion.output

    async def test_unrecognised_failing_command_yields_no_evidence(self) -> None:
        """The test-command set is narrow by design: an unrecognised command
        that merely exited non-zero is not a validation run."""
        runner = _FakeRunner(
            files={"tests/test_widget.py": "..."},
            terminal_runs=(TerminalRun("ls /nope", 2, "No such file"),),
        )
        completion = await OpenHandsGenerateEngine(
            _config(), runner=runner
        ).invoke(_request())
        assert "gate_b_evidence" not in completion.output

    async def test_artifact_id_falls_back_to_the_command(self) -> None:
        """A failing validation run with no test-looking file still proves
        red — the command becomes the artifact reference."""
        runner = _FakeRunner(
            files={"src/widget.py": "x = 1"},
            terminal_runs=(TerminalRun("npm test", 1, "1 failing"),),
        )
        completion = await OpenHandsGenerateEngine(
            _config(), runner=runner
        ).invoke(_request())
        assert completion.output["gate_b_evidence"]["artifact_id"] == "npm test"

    async def test_blocked_run_carries_no_evidence(self) -> None:
        runner = _FakeRunner(
            status="ERROR",
            terminal_runs=(TerminalRun("pytest", 1, "red"),),
        )
        completion = await OpenHandsGenerateEngine(
            _config(), runner=runner
        ).invoke(_request())
        assert completion.status is EscalationStatus.BLOCKED
        assert "gate_b_evidence" not in (completion.output or {})


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
