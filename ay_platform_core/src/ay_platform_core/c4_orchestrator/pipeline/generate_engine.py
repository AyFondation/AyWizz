# =============================================================================
# File: generate_engine.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c4_orchestrator/pipeline/generate_engine.py
# Description: Pluggable engine for the `generate` phase (synthesis-v4, V2 #2,
#              R-200-029). The OpenHands agentic harness is encapsulated BEHIND
#              this seam so the rest of C4 (state machine, gates,
#              materialisation) is untouched and the backend stays swappable.
#              Same envelope as the dispatcher : `invoke(DispatchRequest) ->
#              AgentCompletion`.
#
#              GATED + opt-in : the orchestrator only routes `generate`
#              through an engine when `C4_GENERATE_ENGINE == "openhands"`.
#              Default `in_process` keeps today's dispatcher path EXACTLY
#              as-is (factory returns None).
#
#              v2 (2026-05-22) — Q13 POC adapter. Runs the OpenHands V1 SDK
#              (`openhands-sdk` + `openhands-tools`) routed through C8/LiteLLM
#              (R-200-029 : never a provider directly ; model is a C8
#              `model_list` name prefixed `litellm_proxy/`). The blocking SDK
#              run is delegated to a `runner` callable (the SOLE importer of
#              `openhands.*`, per R-200-029) so the mapping logic is unit-
#              testable with a fake runner. Any runner failure becomes a LOUD
#              `BLOCKED` completion — a gated experimental engine SHALL NOT
#              crash the orchestrator.
#
#              v3 (2026-09-09) — the engine now EMITS `gate_b_evidence`,
#              because it can OBSERVE it. `TerminalObservation` carries
#              `command` / `exit_code` / output `text`, and the conversation's
#              append-only log is readable at `conversation.state.events`. The
#              runner records every executed command into
#              `_RunOutcome.terminal_runs` ; `_gate_b_evidence` then emits the
#              block ONLY when a validation command actually ran and actually
#              FAILED — the TDD red-first proof — carrying the observed
#              command, exit code and output tail so a reviewer can check the
#              claim instead of trusting it.
#
#              It still never fabricates: no failing validation run means no
#              evidence and Gate B blocks (R-200-011). What changed is that
#              honesty no longer costs the engine the gate.
#
#              v2 POC scope (Q1) emitted NO evidence at all, which was the
#              documented finding that motivated this: the engine that could
#              genuinely prove red-first blocked, while the in-process
#              dispatcher passed Gate B by deriving `validation_runs_red` from
#              a FILENAME. That derivation was corrected in the same release
#              (see `dispatcher/in_process.py::_derive_gate_b_evidence`).
#              `openhands-ai` is an OPTIONAL extra installed only in the C15
#              runner image.
#
#              NOTE : R-200-029 (the OpenHands-as-engine contract) is a
#              PROPOSED requirement in the synthesis (§9, gated behind the
#              Q13 POC per "POC before spec amendment"). It is NOT yet
#              declared in the spec corpus, so no `@relation implements:`
#              marker is claimed FOR IT here — that marker is added once the
#              POC passes and the requirement is ratified.
#
#              R-200-011 is a different matter: it IS in the corpus, and since
#              v3 this module produces the Gate B evidence it governs ("the
#              validation artifact has been written AND demonstrated to fail
#              as expected"). The marker below claims that, and only that.
#
# @relation implements:R-200-011
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.models import (
    AgentBlocker,
    AgentCompletion,
    EscalationStatus,
)
from ay_platform_core.c8_llm.registry.key_provider import ModelResolver

_log = logging.getLogger("c4_orchestrator.generate_engine")

# Workspace sub-trees never materialised into `output.files` — runtime /
# agent internals, not produced artefacts.
_WORKSPACE_IGNORE_DIRS = frozenset(
    {".git", ".aywiz", ".openhands", "__pycache__", "node_modules", ".venv"}
)
# Per-file size guard (POC) — skip anything larger than this when reading the
# workspace back, so a stray build artefact can't blow up the completion.
_MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class OpenHandsEngineConfig:
    """Wiring for the OpenHands engine. `gateway_url` + `api_key` point at
    the C8/LiteLLM proxy ; `model` is a C8 `model_list` name prefixed
    `litellm_proxy/` so the OpenHands LiteLLM client routes THROUGH the
    proxy (R-200-029), never a provider directly."""

    gateway_url: str
    api_key: str | None
    # FALLBACK ONLY, and normally EMPTY. The model is resolved per call from the
    # operator's registry catalogue — the agent's quality tier → the project's
    # enabled model (D-011: registering + associating a model in the HMI is
    # enough, no model or provider name in config). This field is used only when
    # that resolution yields nothing, so a deployment that has not yet been
    # configured through the HMI can still be pinned by hand. Leave it empty in
    # any environment where the registry is populated.
    model: str = ""
    max_iterations: int = 50
    workspace_root: str | None = None


# Output kept per observed command, so a `gate_b_evidence` block carries the
# real proof without dragging a whole pytest log into the completion.
_MAX_OBSERVED_OUTPUT_CHARS = 4000

# Command tokens that mark a validation run. Deliberately narrow: a command we
# do not recognise yields NO evidence, which blocks honestly. Widening this set
# is a decision about what counts as proof — never a convenience.
_TEST_COMMAND_TOKENS = (
    "pytest",
    "unittest",
    "npm test",
    "npm run test",
    "vitest",
    "jest",
    "go test",
    "cargo test",
)


@dataclass(frozen=True, slots=True)
class TerminalRun:
    """One command the agent ACTUALLY executed, read off a terminal
    observation (`command` / `exit_code` / output `text`). SDK-free by
    construction so the evidence mapping is unit-testable with fakes."""

    command: str
    exit_code: int | None
    output: str = ""


@dataclass(frozen=True, slots=True)
class _RunOutcome:
    """What the runner reports back from one OpenHands conversation. Kept
    deliberately small + SDK-free so the mapping logic is testable without
    `openhands-ai` installed."""

    workspace: Path
    execution_status: str  # ConversationExecutionStatus member name
    detail: str | None = None
    # Every command the agent ran, in order. This is what turns Gate B
    # evidence from an assertion into a RECORD (see `_gate_b_evidence`).
    terminal_runs: tuple[TerminalRun, ...] = ()


class OpenHandsUnavailableError(RuntimeError):
    """Raised by the runner when the optional `openhands-*` extra is absent
    from the running image (it lives only in the C15 runner image)."""


@runtime_checkable
class GenerateEngine(Protocol):
    """A `generate`-phase backend. Returns the SAME `AgentCompletion`
    envelope a dispatcher would, so the orchestrator's downstream
    materialisation + gate logic is identical regardless of engine."""

    async def invoke(self, request: DispatchRequest) -> AgentCompletion: ...


# A blocking callable that runs one OpenHands conversation. Injected into the
# engine so tests can substitute a fake (mocking the DEPENDENCY, never the
# engine under test — CLAUDE.md §10.2).
Runner = Callable[[OpenHandsEngineConfig, DispatchRequest], _RunOutcome]


def _default_runner(
    config: OpenHandsEngineConfig, request: DispatchRequest
) -> _RunOutcome:
    """Run one OpenHands conversation for the GENERATE phase. The ONLY place
    `openhands.*` is imported (R-200-029). Blocking — invoked via
    `asyncio.to_thread`. The agent works in a fresh temp workspace ; the
    caller reads the produced files back and cleans up."""
    try:
        from openhands.sdk import (  # noqa: PLC0415
            LLM,
            Agent,
            Conversation,
            Tool,
        )
        from openhands.tools.file_editor import FileEditorTool  # noqa: PLC0415
        from openhands.tools.task_tracker import TaskTrackerTool  # noqa: PLC0415
        from openhands.tools.terminal import TerminalTool  # noqa: PLC0415
    except ImportError as exc:  # optional extra not in this image
        raise OpenHandsUnavailableError(str(exc)) from exc

    workspace = Path(
        tempfile.mkdtemp(prefix=f"oh-{request.run_id}-", dir=config.workspace_root)
    )
    llm = LLM(
        model=config.model,
        base_url=config.gateway_url,
        api_key=config.api_key,
        # R-200-035 : per-turn attribution headers forwarded to C8 so the
        # `llm_calls` rows aggregate per run / agent / phase.
        extra_headers={
            "X-Run-Id": request.run_id,
            "X-Sub-Agent-Id": request.run_id,
            "X-Agent-Name": request.agent.value,
            "X-Phase": request.phase.value,
        },
        usage_id=request.run_id,
    )
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        max_iteration_per_run=config.max_iterations,
    )
    conversation.send_message(request.prompt)
    conversation.run()
    return _RunOutcome(
        workspace=workspace,
        execution_status=conversation.state.execution_status.name,
        terminal_runs=_terminal_runs_from_events(
            getattr(conversation.state, "events", ()) or ()
        ),
    )


def _terminal_runs_from_events(events: Any) -> tuple[TerminalRun, ...]:
    """Extract the executed commands from a conversation event log.

    DUCK-TYPED on purpose — no `openhands.*` import, so this is unit-testable
    with plain fakes and survives the SDK moving its class names. A terminal
    OBSERVATION is recognised by carrying both a `command` and an `exit_code`
    attribute ; the matching ACTION carries `command` but no `exit_code`, and
    is therefore skipped (it records intent, not result). The observation is
    taken from the event itself or from a nested `observation` attribute,
    since the SDK wraps observations in events.

    Output text lives in the observation's inherited `text` field — there is
    no separate `stdout`/`output` field on `TerminalObservation`.
    """
    runs: list[TerminalRun] = []
    for event in events:
        for carrier in (getattr(event, "observation", None), event):
            if carrier is None or not hasattr(carrier, "exit_code"):
                continue
            command = getattr(carrier, "command", None)
            if not isinstance(command, str) or not command.strip():
                continue
            exit_code = getattr(carrier, "exit_code", None)
            text = getattr(carrier, "text", "") or ""
            runs.append(
                TerminalRun(
                    command=command.strip(),
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                    output=str(text)[-_MAX_OBSERVED_OUTPUT_CHARS:],
                )
            )
            break
    return tuple(runs)


def _is_test_command(command: str) -> bool:
    """Whether a command reads as a validation run. Narrow by design."""
    lowered = command.lower()
    return any(token in lowered for token in _TEST_COMMAND_TOKENS)


def _gate_b_evidence(
    runs: tuple[TerminalRun, ...], files: list[dict[str, str]]
) -> dict[str, Any] | None:
    """Build `gate_b_evidence` from what the agent OBSERVABLY did.

    Gate B asserts TDD red-first, so the proof is a validation command that
    actually ran and actually FAILED (non-zero exit). Anything else — no test
    command, or one that only ever ran green — yields None, and Gate B blocks.
    That is the honest outcome, not a defect: R-200-011.

    The `observed_*` fields carry the proof itself (command, exit code, output
    tail) so a reviewer can check the claim rather than trust it. Gate B reads
    its own keys via `.get()` and ignores the extras.
    """
    from datetime import UTC, datetime  # noqa: PLC0415 — cold path

    red = next(
        (
            run
            for run in runs
            if _is_test_command(run.command)
            and run.exit_code is not None
            and run.exit_code != 0
        ),
        None,
    )
    if red is None:
        return None
    return {
        "artifact_id": _test_artifact_path(files) or red.command,
        "validation_artifact_exists": True,
        "validation_runs_red": True,
        "evidence_timestamp": datetime.now(UTC).isoformat(),
        "observed_command": red.command,
        "observed_exit_code": red.exit_code,
        "observed_output_tail": red.output,
    }


def _test_artifact_path(files: list[dict[str, str]]) -> str | None:
    """First produced file that reads as a test artifact, for `artifact_id`.
    Reuses the dispatcher's heuristic rather than re-declaring one (§8.4 /
    `check_no_parallel_definitions`). Imported locally to keep the module
    import graph acyclic."""
    from ay_platform_core.c4_orchestrator.dispatcher.in_process import (  # noqa: PLC0415
        _looks_like_test_path,
    )

    for entry in files:
        path = entry.get("path")
        if isinstance(path, str) and _looks_like_test_path(path):
            return path
    return None


class OpenHandsGenerateEngine:
    """OpenHands harness for the `generate` phase (R-200-029, gated on Q13).

    Routes through C8/LiteLLM. The blocking SDK run is delegated to a
    `runner` (default `_default_runner`, the sole importer of `openhands.*`)
    so this mapping layer is unit-testable with a fake. Any runner failure
    becomes a loud `BLOCKED` completion : the orchestrator must never abort
    a run because a gated, experimental engine raised."""

    def __init__(
        self,
        config: OpenHandsEngineConfig,
        *,
        runner: Runner = _default_runner,
        model_resolver: ModelResolver | None = None,
    ) -> None:
        self._config = config
        self._runner = runner
        # Resolves the agent's call to a model alias from the OPERATOR'S
        # registry catalogue — the same path every other LLM caller uses. See
        # `_config_for`. None → the configured fallback only.
        self._model_resolver = model_resolver

    async def _config_for(self, request: DispatchRequest) -> OpenHandsEngineConfig:
        """Per-call config whose `model` comes from the platform's own LLM
        configuration rather than from this component's environment.

        The registry resolver maps the agent to a quality tier and returns the
        model the operator enabled FOR THIS PROJECT in the HMI (D-011). That is
        the whole point: the engine must not be the one component that pins a
        model in a manifest while every other caller honours the application's
        configuration.

        `require_tool_calling=True` is not optional here — an agent loop that
        cannot call tools cannot edit a file or run a test, so a model without
        that capability is not a degraded choice, it is a broken one.

        Best-effort by design: no resolver, no configured project model, or a
        registry error all fall back to `config.model`, and an empty fallback
        leaves the SDK to fail loudly rather than silently calling something
        nobody chose.
        """
        if self._model_resolver is None:
            return self._config
        try:
            alias = await self._model_resolver(
                request.agent.value,
                request.tenant_id,
                request.project_id,
                require_tool_calling=True,
            )
        except Exception as exc:  # broad by design: never break a run on this
            _log.warning("model resolution failed for run %s: %s", request.run_id, exc)
            return self._config
        if not alias:
            _log.info(
                "no project model configured for run %s — falling back to %r",
                request.run_id,
                self._config.model,
            )
            return self._config
        return replace(self._config, model=f"litellm_proxy/{alias}")

    async def invoke(self, request: DispatchRequest) -> AgentCompletion:
        config = await self._config_for(request)
        try:
            outcome = await asyncio.to_thread(self._runner, config, request)
        except OpenHandsUnavailableError as exc:
            return self._blocked(
                request,
                f"openhands extra is not installed in this image: {exc}",
                "Run the orchestrator from the C15 runner image "
                "(ay_platform_core[openhands]) or set "
                "C4_GENERATE_ENGINE=in_process.",
            )
        except Exception as exc:
            # POC: surface ANY SDK / runtime failure as a loud BLOCKED rather
            # than propagating — a gated engine must not crash the run.
            _log.exception(
                "OpenHands generate run failed (run_id=%s)", request.run_id
            )
            return self._blocked(
                request,
                f"OpenHands run failed: {exc!r}",
                "Inspect the C15 pod logs / OTel trace ; this is a POC adapter.",
            )

        try:
            files = self._collect_files(outcome.workspace)
        finally:
            shutil.rmtree(outcome.workspace, ignore_errors=True)
        return self._map_outcome(request, outcome, files)

    @staticmethod
    def _collect_files(workspace: Path) -> list[dict[str, str]]:
        """Read produced text files back from the workspace as `output.files`
        (`{"path", "content"}`). Skips runtime internals, binaries, and
        oversized files."""
        files: list[dict[str, str]] = []
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace)
            if any(part in _WORKSPACE_IGNORE_DIRS for part in rel.parts):
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary / unreadable artefact — not a source file
            files.append({"path": rel.as_posix(), "content": content})
        return files

    def _map_outcome(
        self,
        request: DispatchRequest,
        outcome: _RunOutcome,
        files: list[dict[str, str]],
    ) -> AgentCompletion:
        """Map the OpenHands terminal status onto an `AgentCompletion`.

        FINISHED → DONE with `output.files`, plus `gate_b_evidence` WHEN the
        agent was observed running a validation command that failed — the
        red-first proof (see `_gate_b_evidence`). Since 2026-09-09 this engine
        emits that evidence because it can OBSERVE it: the terminal
        observations carry the command and its exit code. It still never
        fabricates — no failing validation run means no evidence block and
        Gate B blocks, exactly as before.

        ERROR / STUCK / any non-FINISHED terminal state → BLOCKED."""
        if outcome.execution_status == "FINISHED":
            output: dict[str, Any] = {"engine": "openhands", "files": files}
            evidence = _gate_b_evidence(outcome.terminal_runs, files)
            if evidence is not None:
                output["gate_b_evidence"] = evidence
            return AgentCompletion(
                agent=request.agent,
                run_id=request.run_id,
                phase=request.phase,
                status=EscalationStatus.DONE,
                output=output,
            )
        detail = f" ({outcome.detail})" if outcome.detail else ""
        return self._blocked(
            request,
            f"OpenHands ended in non-FINISHED state: "
            f"{outcome.execution_status}{detail}",
            "Inspect the OpenHands event stream / OTel trace for this run.",
        )

    @staticmethod
    def _blocked(
        request: DispatchRequest, reason: str, suggested_action: str
    ) -> AgentCompletion:
        return AgentCompletion(
            agent=request.agent,
            run_id=request.run_id,
            phase=request.phase,
            status=EscalationStatus.BLOCKED,
            blocker=AgentBlocker(reason=reason, suggested_action=suggested_action),
        )


def build_generate_engine(
    name: str,
    config: OpenHandsEngineConfig | None = None,
    *,
    model_resolver: ModelResolver | None = None,
) -> GenerateEngine | None:
    """Resolve the `C4_GENERATE_ENGINE` flag to an engine, or None to keep
    the default dispatcher path (`in_process`). Unknown values fall back to
    None (dispatcher) — fail-safe, never crashes pod boot.

    `model_resolver` is what makes the engine honour the platform's own LLM
    configuration instead of a pinned env value; omit it only where no registry
    is available (unit tests)."""
    if name == "openhands":
        cfg = config or OpenHandsEngineConfig(gateway_url="", api_key=None)
        return OpenHandsGenerateEngine(cfg, model_resolver=model_resolver)
    return None
