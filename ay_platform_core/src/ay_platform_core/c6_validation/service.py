# =============================================================================
# File: service.py
# Version: 3
# Path: ay_platform_core/src/ay_platform_core/c6_validation/service.py
# Description: ValidationService — the C6 facade. Orchestrates:
#              - plugin discovery (via the registry)
#              - run lifecycle (trigger, execute, persist)
#              - parsing `@relation` markers from artifacts
#              - writing findings to ArangoDB + an immutable JSON snapshot
#                to MinIO.
#              - graded verdicts : T1 deterministic (always) + the opt-in
#                T3 LLM-as-judge (v2, D-017 / R-700-032).
#              - quality-regression detection vs the project's previous run
#                (v3, D-017 / R-700-033).
#
# @relation implements:R-700-010
# @relation implements:R-700-011
# @relation implements:R-700-012
# @relation implements:R-700-013
# @relation implements:R-700-014
# @relation implements:R-700-032
# @relation implements:R-700-033
# =============================================================================

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request, status

from ay_platform_core.c6_validation.config import ValidationConfig
from ay_platform_core.c6_validation.db.repository import ValidationRepository
from ay_platform_core.c6_validation.domains.code.parsers import (
    MarkerSyntaxError,
    extract_markers,
)
from ay_platform_core.c6_validation.domains.code.plugin import set_current_run_id
from ay_platform_core.c6_validation.grading import grade_deterministic
from ay_platform_core.c6_validation.grading_judge import grade_judged
from ay_platform_core.c6_validation.models import (
    CheckContext,
    CodeArtifact,
    Finding,
    FindingPage,
    PluginDescriptor,
    RelationMarker,
    RunStatus,
    RunSummaryCounts,
    RunTriggerRequest,
    RunTriggerResponse,
    Severity,
    ValidationRun,
    Verdict,
)
from ay_platform_core.c6_validation.plugin.base import ValidationPlugin
from ay_platform_core.c6_validation.plugin.registry import PluginRegistry
from ay_platform_core.c6_validation.regression import detect_regressions
from ay_platform_core.c6_validation.storage.minio_storage import (
    ValidationSnapshotStorage,
)
from ay_platform_core.c8_llm.client import LLMGatewayClient


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


def _check_enabled_env_var(check_id: str) -> str:
    slug = check_id.upper().replace("-", "_")
    return f"C6_CHECK_{slug}_ENABLED"


def _check_enabled(check_id: str, *, default: bool) -> bool:
    raw = os.environ.get(_check_enabled_env_var(check_id))
    if raw is None:
        return default
    return raw.strip().lower() not in {"false", "0", "no", "off"}


class ValidationService:
    """Facade used by the FastAPI router and by integration tests."""

    def __init__(
        self,
        config: ValidationConfig,
        registry: PluginRegistry,
        repo: ValidationRepository,
        snapshot_store: ValidationSnapshotStorage | None = None,
        llm_client: LLMGatewayClient | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._repo = repo
        self._snapshots = snapshot_store
        # Optional C8 client for the T3 LLM-as-judge (R-700-032). When absent
        # (every test fixture, and any deployment that doesn't wire it) the
        # judge is simply never invoked — T3 stays dormant, T1 is unaffected.
        self._llm_client = llm_client
        self._background_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[PluginDescriptor]:
        return self._registry.describe_all()

    def list_domains(self) -> list[str]:
        return self._registry.domains()

    async def alist_plugins(self) -> list[PluginDescriptor]:
        """Async alias — implemented so the C9 remote adapter exposes the
        same method surface while keeping the in-process call cost a no-op.
        """
        return self.list_plugins()

    async def alist_domains(self) -> list[str]:
        return self.list_domains()

    async def get_run(self, run_id: str) -> ValidationRun:
        row = await self._repo.get_run(run_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="run not found"
            )
        return _run_from_row(row)

    async def list_findings(
        self, run_id: str, *, limit: int = 100, offset: int = 0
    ) -> FindingPage:
        # Check run exists (returns 404 otherwise).
        await self.get_run(run_id)
        total, items = await self._repo.list_findings_for_run(
            run_id, limit=limit, offset=offset
        )
        return FindingPage(
            run_id=run_id,
            total=total,
            items=[Finding(**item) for item in items],
        )

    async def get_finding(self, finding_id: str) -> Finding:
        row = await self._repo.get_finding(finding_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="finding not found"
            )
        return Finding(**row)

    # ------------------------------------------------------------------
    # Run trigger + async execution
    # ------------------------------------------------------------------

    async def trigger_run(
        self,
        payload: RunTriggerRequest,
        *,
        requirements: list[dict[str, Any]],
        artifacts: list[CodeArtifact],
    ) -> RunTriggerResponse:
        """Create a run row (status=pending) and kick off in-process execution.

        Returns as soon as the run row is persisted; actual work proceeds in
        the background via ``asyncio.create_task`` (R-700-011).
        """
        plugins = self._plugins_for_domain(payload.domain)
        if not plugins:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no plugin registered for domain {payload.domain!r}",
            )

        run_id = _new_id()
        started = _now()
        run = ValidationRun(
            run_id=run_id,
            project_id=payload.project_id,
            domain=payload.domain,
            check_ids=list(payload.check_ids),
            status=RunStatus.PENDING,
            started_at=started,
        )
        await self._repo.upsert_run(_run_to_row(run))

        # Fire-and-forget execution. Each run creates its own task so the
        # caller returns 202 immediately. The task handle is retained on the
        # service instance so it is not garbage-collected mid-flight (RUF006).
        task = asyncio.create_task(
            self._execute_run_safely(
                run_id=run_id,
                payload=payload,
                requirements=requirements,
                artifacts=artifacts,
                started=started,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return RunTriggerResponse(run_id=run_id, status=RunStatus.PENDING)

    async def execute_run_sync(
        self,
        payload: RunTriggerRequest,
        *,
        requirements: list[dict[str, Any]],
        artifacts: list[CodeArtifact],
    ) -> ValidationRun:
        """Integration-test entrypoint: run synchronously and return the final row.

        Goes through the same code path as ``trigger_run`` but awaits
        completion. Integration tests use this to avoid racing on
        ``asyncio.create_task``.
        """
        plugins = self._plugins_for_domain(payload.domain)
        if not plugins:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no plugin registered for domain {payload.domain!r}",
            )
        run_id = _new_id()
        started = _now()
        run = ValidationRun(
            run_id=run_id,
            project_id=payload.project_id,
            domain=payload.domain,
            check_ids=list(payload.check_ids),
            status=RunStatus.PENDING,
            started_at=started,
        )
        await self._repo.upsert_run(_run_to_row(run))
        await self._execute_run_safely(
            run_id=run_id,
            payload=payload,
            requirements=requirements,
            artifacts=artifacts,
            started=started,
        )
        row = await self._repo.get_run(run_id)
        if row is None:
            raise RuntimeError("run vanished after execution")
        return _run_from_row(row)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _plugins_for_domain(self, domain: str) -> list[ValidationPlugin]:
        return self._registry.plugins_for_domain(domain)

    async def _detect_regressions(
        self,
        *,
        run_id: str,
        payload: RunTriggerRequest,
        current_findings: list[Finding],
        current_score: float,
    ) -> list[Finding]:
        """Compare this run to the project's previous completed run and return
        ADVISORY regression findings (R-700-033). Best-effort : any read error,
        or simply no prior run, yields ``[]`` — regression detection SHALL NOT
        fail a run."""
        try:
            prev_row = await self._repo.get_latest_completed_run(
                payload.project_id, payload.domain
            )
            if prev_row is None:
                return []
            prev_run = _run_from_row(prev_row)
            prev_score = (
                prev_run.verdict.score if prev_run.verdict is not None else 1.0
            )
            _total, prev_items = await self._repo.list_findings_for_run(
                prev_run.run_id,
                limit=self._config.max_findings_per_run,
                offset=0,
            )
            previous_findings = [Finding(**row) for row in prev_items]
        except Exception:
            return []
        return detect_regressions(
            run_id=run_id,
            domain=payload.domain,
            current_findings=current_findings,
            current_score=current_score,
            previous_run_id=prev_run.run_id,
            previous_findings=previous_findings,
            previous_score=prev_score,
        )

    async def _execute_run_safely(
        self,
        *,
        run_id: str,
        payload: RunTriggerRequest,
        requirements: list[dict[str, Any]],
        artifacts: list[CodeArtifact],
        started: datetime,
    ) -> None:
        """Orchestrate check execution with global error containment.

        A failure of the orchestration layer itself (vs a single plugin
        error) marks the run as FAILED. Per-check errors are translated
        to `severity=info` findings by _execute_run.
        """
        try:
            await self._execute_run(
                run_id=run_id,
                payload=payload,
                requirements=requirements,
                artifacts=artifacts,
                started=started,
            )
        except Exception as exc:
            await self._repo.upsert_run(
                _run_to_row(
                    ValidationRun(
                        run_id=run_id,
                        project_id=payload.project_id,
                        domain=payload.domain,
                        check_ids=list(payload.check_ids),
                        status=RunStatus.FAILED,
                        started_at=started,
                        completed_at=_now(),
                    )
                )
            )
            await self._repo.insert_findings(
                [
                    _finding_to_row(
                        Finding(
                            finding_id=_new_id(),
                            run_id=run_id,
                            check_id="orchestration:error",
                            domain=payload.domain,
                            severity=Severity.INFO,
                            message=f"run orchestration failed: {type(exc).__name__}: {exc}",
                            created_at=_now(),
                        )
                    )
                ]
            )

    async def _execute_run(
        self,
        *,
        run_id: str,
        payload: RunTriggerRequest,
        requirements: list[dict[str, Any]],
        artifacts: list[CodeArtifact],
        started: datetime,
    ) -> None:
        # Transition to RUNNING.
        await self._repo.upsert_run(
            _run_to_row(
                ValidationRun(
                    run_id=run_id,
                    project_id=payload.project_id,
                    domain=payload.domain,
                    check_ids=list(payload.check_ids),
                    status=RunStatus.RUNNING,
                    started_at=started,
                )
            )
        )

        # Pre-parse every artifact once, for efficiency and to give the
        # marker-syntax check somewhere to live.
        all_markers: list[RelationMarker] = []
        marker_errors: list[MarkerSyntaxError] = []
        for artifact in artifacts:
            markers, errors = extract_markers(artifact)
            all_markers.extend(markers)
            marker_errors.extend(errors)

        context = CheckContext(
            project_id=payload.project_id,
            domain=payload.domain,
            requirements=requirements,
            artifacts=artifacts,
            markers=all_markers,
        )

        findings: list[Finding] = [
            Finding(
                finding_id=_new_id(),
                run_id=run_id,
                check_id="marker-syntax",
                domain=payload.domain,
                severity=Severity.BLOCKING,
                artifact_ref=err.artifact_path,
                location=f"{err.artifact_path}:{err.line}",
                message=f"Malformed `@relation` marker: {err.reason} (line: {err.raw!r})",
                created_at=_now(),
            )
            for err in marker_errors
        ]

        plugins = self._plugins_for_domain(payload.domain)
        set_current_run_id(run_id)

        # Build the list of checks to execute. If the user specified a
        # non-empty list, intersect with what the plugins declare; otherwise
        # run every declared check.
        selected_check_ids = (
            set(payload.check_ids) if payload.check_ids else None
        )

        plugin_errored_count = 0
        plugin_ok_count = 0
        for plugin in plugins:
            for spec in plugin.describe().checks:
                if selected_check_ids is not None and spec.check_id not in selected_check_ids:
                    continue
                if not _check_enabled(
                    spec.check_id, default=self._config.default_check_enabled
                ):
                    findings.append(
                        Finding(
                            finding_id=_new_id(),
                            run_id=run_id,
                            check_id=f"{spec.check_id}:disabled",
                            domain=payload.domain,
                            severity=Severity.INFO,
                            message=f"Check {spec.check_id} disabled via config.",
                            created_at=_now(),
                        )
                    )
                    continue
                result = await plugin.run_check(spec.check_id, context)
                if result.error_message is not None:
                    plugin_errored_count += 1
                    findings.append(
                        Finding(
                            finding_id=_new_id(),
                            run_id=run_id,
                            check_id=f"{spec.check_id}:error",
                            domain=payload.domain,
                            severity=Severity.INFO,
                            message=result.error_message,
                            created_at=_now(),
                        )
                    )
                else:
                    plugin_ok_count += 1
                    findings.extend(result.findings)

        # Truncate if the run exploded (R-? guardrail from config).
        if len(findings) > self._config.max_findings_per_run:
            findings = findings[: self._config.max_findings_per_run]
            findings.append(
                Finding(
                    finding_id=_new_id(),
                    run_id=run_id,
                    check_id="run:truncated",
                    domain=payload.domain,
                    severity=Severity.INFO,
                    message=(
                        "findings list truncated at "
                        f"{self._config.max_findings_per_run} — downstream "
                        "consumers should re-run with tighter filters"
                    ),
                    created_at=_now(),
                )
            )

        # D-017 / R-700-031 : T1 deterministic verdict graded from the CHECK
        # findings — computed BEFORE the regression notices below are appended,
        # so the score measures this run's coherence only (R-700-033
        # anti-feedback : a regression must never lower the score the next run
        # compares against).
        verdict = grade_deterministic(
            findings, run_id=run_id, domain=payload.domain
        )

        # D-017 / R-700-033 : quality-regression detection vs the project's
        # previous completed run. Appends ADVISORY findings (never affects the
        # verdict above). Best-effort read — an empty history is the norm.
        regression_findings = await self._detect_regressions(
            run_id=run_id,
            payload=payload,
            current_findings=findings,
            current_score=verdict.score,
        )
        findings.extend(regression_findings)

        await self._repo.insert_findings([_finding_to_row(f) for f in findings])

        counts = _summarise(findings)
        # D-017 / R-700-032 : opt-in T3 LLM-as-judge verdict. Best-effort —
        # `grade_judged` returns None on any failure, leaving T1 untouched.
        judged_verdict: Verdict | None = None
        if self._config.judge_enabled and self._llm_client is not None:
            judged_verdict = await grade_judged(
                llm_client=self._llm_client,
                run_id=run_id,
                domain=payload.domain,
                requirements=requirements,
                artifacts=artifacts,
                agent_name=self._config.judge_agent,
                project_id=payload.project_id,
            )
        completed = _now()
        # R-700-014: if every check errored, transition to FAILED; otherwise
        # COMPLETED regardless of blocking findings (blocking is a QUALITY
        # signal, not a run-level failure).
        if plugin_ok_count == 0 and plugin_errored_count > 0:
            final_status = RunStatus.FAILED
        else:
            final_status = RunStatus.COMPLETED

        snapshot_uri: str | None = None
        if self._snapshots is not None:
            run_row_for_snapshot = ValidationRun(
                run_id=run_id,
                project_id=payload.project_id,
                domain=payload.domain,
                check_ids=list(payload.check_ids),
                status=final_status,
                findings_count=counts,
                verdict=verdict,
                judged_verdict=judged_verdict,
                started_at=started,
                completed_at=completed,
            )
            snapshot_path = ValidationSnapshotStorage.snapshot_path(
                payload.project_id, run_id
            )
            snapshot_body = {
                "run": run_row_for_snapshot.model_dump(mode="json"),
                "findings": [f.model_dump(mode="json") for f in findings],
            }
            await self._snapshots.put_snapshot(
                snapshot_path, json.dumps(snapshot_body, indent=2).encode("utf-8")
            )
            snapshot_uri = snapshot_path

        await self._repo.upsert_run(
            _run_to_row(
                ValidationRun(
                    run_id=run_id,
                    project_id=payload.project_id,
                    domain=payload.domain,
                    check_ids=list(payload.check_ids),
                    status=final_status,
                    findings_count=counts,
                    verdict=verdict,
                    judged_verdict=judged_verdict,
                    started_at=started,
                    completed_at=completed,
                    snapshot_uri=snapshot_uri,
                )
            )
        )


# ---------------------------------------------------------------------------
# Row ↔ model helpers
# ---------------------------------------------------------------------------


def _run_to_row(run: ValidationRun) -> dict[str, Any]:
    return run.model_dump(mode="json")


def _run_from_row(row: dict[str, Any]) -> ValidationRun:
    payload = {k: v for k, v in row.items() if not k.startswith("_")}
    return ValidationRun.model_validate(payload)


def _finding_to_row(finding: Finding) -> dict[str, Any]:
    return finding.model_dump(mode="json")


def _summarise(findings: list[Finding]) -> RunSummaryCounts:
    blocking = sum(1 for f in findings if f.severity == Severity.BLOCKING)
    advisory = sum(1 for f in findings if f.severity == Severity.ADVISORY)
    info = sum(1 for f in findings if f.severity == Severity.INFO)
    return RunSummaryCounts(blocking=blocking, advisory=advisory, info=info)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_service(request: Request) -> ValidationService:
    svc = getattr(request.app.state, "validation_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="validation service not initialised",
        )
    return svc  # type: ignore[no-any-return]
