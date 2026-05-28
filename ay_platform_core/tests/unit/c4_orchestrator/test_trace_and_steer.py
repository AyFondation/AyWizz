# =============================================================================
# File: test_trace_and_steer.py
# Version: 1
# Path: ay_platform_core/tests/unit/c4_orchestrator/test_trace_and_steer.py
# Description: Unit tests for Tranche B helpers (R-200-200..205) on the
#              C4 OrchestratorService :
#                - `_append_trace`  : append-only ledger semantics ;
#                - `_drain_pending_steer` : FIFO drain that clears
#                  the queue and returns chronological messages ;
#                - `_public` projection : trace window N=200 newest-first.
#              No I/O — the service is built with mock collaborators
#              that don't touch Arango / MinIO / LLM.
#
# @relation validates:R-200-200
# @relation validates:R-200-201
# @relation validates:R-200-202
# @relation validates:R-200-203
# =============================================================================

from __future__ import annotations

from typing import Any

import pytest

from ay_platform_core.c4_orchestrator.config import OrchestratorConfig
from ay_platform_core.c4_orchestrator.models import (
    Phase,
    RunStatus,
    TraceEventKind,
)
from ay_platform_core.c4_orchestrator.service import (
    _PUBLIC_TRACE_WINDOW,
    OrchestratorService,
    _public,
)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_key": "run-1",
        "run_id": "run-1",
        "project_id": "p1",
        "session_id": "s1",
        "tenant_id": "t1",
        "user_id": "u1",
        "domain": "code",
        "current_phase": Phase.BRAINSTORM.value,
        "status": RunStatus.RUNNING.value,
        "started_at": "2026-05-20T10:00:00+00:00",
        "completed_at": None,
        "concerns": [],
        "minio_root": "c4-runs/run-1/",
        "trace": [],
        "pending_steer": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def svc() -> OrchestratorService:
    # Trace + steer helpers are pure on `row` — collaborators can be
    # None-typed mocks (never called).
    return OrchestratorService(
        config=OrchestratorConfig(),
        repo=None,  # type: ignore[arg-type]
        dispatcher=None,  # type: ignore[arg-type]
        domain_plugin=None,  # type: ignore[arg-type]
        publisher=None,  # type: ignore[arg-type]
    )


class TestAppendTrace:
    def test_appends_in_arrival_order(self, svc: OrchestratorService) -> None:
        row = _row()
        svc._append_trace(
            row,
            kind=TraceEventKind.AGENT_DISPATCH,
            phase=Phase.SPEC,
            label="a",
        )
        svc._append_trace(
            row,
            kind=TraceEventKind.GATE_EVAL,
            phase=Phase.SPEC,
            label="b",
            ok=True,
        )
        assert [ev["label"] for ev in row["trace"]] == ["a", "b"]
        assert row["trace"][0]["kind"] == "agent-dispatch"
        assert row["trace"][1]["kind"] == "gate-eval"
        assert row["trace"][1]["ok"] is True

    def test_optional_fields_omitted_when_none(
        self, svc: OrchestratorService,
    ) -> None:
        row = _row()
        svc._append_trace(
            row, kind=TraceEventKind.PHASE_BOUNDARY, phase=Phase.PLAN, label="x",
        )
        ev = row["trace"][0]
        assert "duration_ms" not in ev
        assert "ok" not in ev
        assert "payload" not in ev

    def test_handles_missing_trace_field(self, svc: OrchestratorService) -> None:
        row = _row()
        row.pop("trace")
        svc._append_trace(
            row, kind=TraceEventKind.STEER_APPLIED, phase=Phase.PLAN, label="s",
        )
        assert isinstance(row["trace"], list)
        assert len(row["trace"]) == 1


class TestDrainPendingSteer:
    def test_drain_returns_messages_and_clears_queue(
        self, svc: OrchestratorService,
    ) -> None:
        row = _row(pending_steer=["focus on REST", "skip the README"])
        drained = svc._drain_pending_steer(row)
        assert drained == ["focus on REST", "skip the README"]
        assert row["pending_steer"] == []

    def test_drain_empty_queue_returns_empty_list(
        self, svc: OrchestratorService,
    ) -> None:
        row = _row()
        assert svc._drain_pending_steer(row) == []

    def test_drain_tolerates_missing_field(
        self, svc: OrchestratorService,
    ) -> None:
        row = _row()
        row.pop("pending_steer")
        assert svc._drain_pending_steer(row) == []
        assert row["pending_steer"] == []


class TestPublicProjection:
    def test_trace_projected_newest_first(self) -> None:
        row = _row(trace=[
            {"kind": "agent-dispatch", "ts": "2026-05-20T10:00:00+00:00",
             "phase": "spec", "label": "first"},
            {"kind": "gate-eval", "ts": "2026-05-20T10:00:01+00:00",
             "phase": "spec", "label": "second", "ok": True},
        ])
        pub = _public(row)
        assert [ev.label for ev in pub.trace] == ["second", "first"]

    def test_trace_window_caps_at_200(self) -> None:
        # 300 events stored, public projection keeps the 200 most recent.
        # Spread timestamps over 5 hours / 1 s steps to stay well-formed.
        stored = [
            {
                "kind": "agent-dispatch",
                "ts": (
                    f"2026-05-20T{10 + (i // 3600):02d}:"
                    f"{(i // 60) % 60:02d}:{i % 60:02d}+00:00"
                ),
                "phase": "spec",
                "label": f"e{i}",
            }
            for i in range(300)
        ]
        row = _row(trace=stored)
        pub = _public(row)
        assert len(pub.trace) == _PUBLIC_TRACE_WINDOW == 200
        # Most-recent first : the newest stored event is labeled "e299".
        assert pub.trace[0].label == "e299"
        # Window's oldest visible label is "e100" (the cut point).
        assert pub.trace[-1].label == "e100"

    def test_legacy_run_without_trace_field_returns_empty(self) -> None:
        row = _row()
        row.pop("trace")
        pub = _public(row)
        assert pub.trace == []
