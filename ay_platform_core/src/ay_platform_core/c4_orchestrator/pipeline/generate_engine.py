# =============================================================================
# File: generate_engine.py
# Version: 2
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
#              POC scope (Q1 unresolved) : the engine returns the produced
#              files in `output.files` but emits NO `gate_b_evidence` — it
#              does not fabricate TDD red-first proof it has not verified.
#              Gate B will therefore BLOCK such a completion ; that is a
#              documented finding feeding the Q1 decision (single-shot vs
#              gated sub-steps), NOT a defect. `openhands-ai` is an OPTIONAL
#              extra installed only in the C15 runner image.
#
#              NOTE : R-200-029 (the OpenHands-as-engine contract) is a
#              PROPOSED requirement in the synthesis (§9, gated behind the
#              Q13 POC per "POC before spec amendment"). It is NOT yet
#              declared in the spec corpus, so no `@relation implements:`
#              marker is claimed here — the marker is added once the POC
#              passes and the requirement is ratified.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ay_platform_core.c4_orchestrator.dispatcher.base import DispatchRequest
from ay_platform_core.c4_orchestrator.models import (
    AgentBlocker,
    AgentCompletion,
    EscalationStatus,
)

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
    model: str
    max_iterations: int = 50
    workspace_root: str | None = None


@dataclass(frozen=True, slots=True)
class _RunOutcome:
    """What the runner reports back from one OpenHands conversation. Kept
    deliberately small + SDK-free so the mapping logic is testable without
    `openhands-ai` installed."""

    workspace: Path
    execution_status: str  # ConversationExecutionStatus member name
    detail: str | None = None


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
    )


class OpenHandsGenerateEngine:
    """OpenHands harness for the `generate` phase (R-200-029, gated on Q13).

    Routes through C8/LiteLLM. The blocking SDK run is delegated to a
    `runner` (default `_default_runner`, the sole importer of `openhands.*`)
    so this mapping layer is unit-testable with a fake. Any runner failure
    becomes a loud `BLOCKED` completion : the orchestrator must never abort
    a run because a gated, experimental engine raised."""

    def __init__(
        self, config: OpenHandsEngineConfig, *, runner: Runner = _default_runner
    ) -> None:
        self._config = config
        self._runner = runner

    async def invoke(self, request: DispatchRequest) -> AgentCompletion:
        try:
            outcome = await asyncio.to_thread(self._runner, self._config, request)
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

        FINISHED → DONE with `output.files`. NOTE (Q1, POC) : no
        `gate_b_evidence` is emitted — the engine does not fabricate
        red-first proof, so Gate B will block downstream. That is a
        documented finding, not a defect. ERROR / STUCK / any non-FINISHED
        terminal state → BLOCKED."""
        if outcome.execution_status == "FINISHED":
            return AgentCompletion(
                agent=request.agent,
                run_id=request.run_id,
                phase=request.phase,
                status=EscalationStatus.DONE,
                output={"engine": "openhands", "files": files},
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
    name: str, config: OpenHandsEngineConfig | None = None
) -> GenerateEngine | None:
    """Resolve the `C4_GENERATE_ENGINE` flag to an engine, or None to keep
    the default dispatcher path (`in_process`). Unknown values fall back to
    None (dispatcher) — fail-safe, never crashes pod boot."""
    if name == "openhands":
        cfg = config or OpenHandsEngineConfig(gateway_url="", api_key=None, model="")
        return OpenHandsGenerateEngine(cfg)
    return None
