# =============================================================================
# File: test_quota.py
# Version: 2
# Path: ay_platform_core/tests/unit/c8_llm/test_quota.py
# Description: Unit tests for the FOUR-LEVEL LLM quota subsystem: the clamping
#              validator (user <= project <= tenant <= global), per-subject
#              per-level evaluation (block if ANY level exceeded), warn
#              threshold, calendar vs first-use reset, and the QuotaGuard
#              (best-effort warn, hard raise, subject pass-through).
# =============================================================================

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from ay_platform_core.c8_llm.quota.guard import QuotaExceededError, QuotaGuard
from ay_platform_core.c8_llm.quota.models import QuotaLimits, QuotaPolicy, QuotaWindow
from ay_platform_core.c8_llm.quota.service import QuotaService

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)  # a Monday
_Subject = tuple[str | None, str | None, str | None]


class _FakeStore:
    """In-memory QuotaStore. `usage` maps a subject signature
    (tenant_id, project_id, user_id) → (cost, tokens); missing → (0, 0)."""

    def __init__(
        self,
        usage: dict[_Subject, tuple[float, int]] | None = None,
        oldest: dict[_Subject, str] | None = None,
    ) -> None:
        self.policy_doc: dict[str, Any] | None = None
        self.usage = usage or {}
        self.oldest = oldest or {}
        self.anchors: dict[str, str] = {}

    async def get_policy(self) -> dict[str, Any] | None:
        return self.policy_doc

    async def set_policy(self, doc: dict[str, Any]) -> None:
        self.policy_doc = doc

    async def get_anchor(self, key: str) -> str | None:
        return self.anchors.get(key)

    async def set_anchor(self, key: str, iso: str) -> None:
        self.anchors[key] = iso

    async def usage_in_window(
        self,
        since_iso: str,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[float, int, str | None]:
        key: _Subject = (tenant_id, project_id, user_id)
        cost, tokens = self.usage.get(key, (0.0, 0))
        return (cost, tokens, self.oldest.get(key))


def _svc(store: _FakeStore) -> QuotaService:
    return QuotaService(store, clock=lambda: _NOW)


def _policy(store: _FakeStore, window: QuotaWindow) -> None:
    store.policy_doc = QuotaPolicy(windows=[window]).model_dump(mode="json")


# ---- Clamping validator -----------------------------------------------------


async def test_clamping_rejects_child_exceeding_parent() -> None:
    with pytest.raises(ValueError, match="may not permit more"):
        QuotaWindow(
            key="s",
            label="S",
            duration_seconds=3600,
            limits={
                "tenant": QuotaLimits(max_cost_usd=10.0),
                "user": QuotaLimits(max_cost_usd=20.0),  # > tenant
            },
        )


async def test_clamping_allows_child_below_parent() -> None:
    w = QuotaWindow(
        key="s",
        label="S",
        duration_seconds=3600,
        limits={
            "global": QuotaLimits(max_cost_usd=100.0),
            "tenant": QuotaLimits(max_cost_usd=50.0),
            "user": QuotaLimits(max_cost_usd=10.0),
        },
    )
    assert w.level_limits("user").max_cost_usd == 10.0


# ---- Per-level evaluation ---------------------------------------------------


async def test_default_policy_is_inert() -> None:
    status = await _svc(_FakeStore()).evaluate("t1", user_id="u1")
    assert not status.blocked and not status.warned


async def test_blocks_when_any_level_exceeds() -> None:
    # User limit $10; this user spent $12 → exceeded, even though tenant is fine.
    store = _FakeStore(usage={(None, None, "u1"): (12.0, 0), ("t1", None, None): (1.0, 0)})
    _policy(
        store,
        QuotaWindow(
            key="session",
            label="Session",
            duration_seconds=3600,
            anchor="first_use",
            limits={
                "tenant": QuotaLimits(max_cost_usd=1000.0),
                "user": QuotaLimits(max_cost_usd=10.0),
            },
        ),
    )
    status = await _svc(store).evaluate("t1", user_id="u1")
    assert status.blocked
    user_level = next(s for s in status.windows[0].levels if s.level == "user")
    assert user_level.state == "exceeded"
    # The flat mirror reflects the most-constraining (user) level.
    assert status.windows[0].cost_pct == 120.0


async def test_global_aggregate_is_unfiltered() -> None:
    store = _FakeStore(usage={(None, None, None): (90.0, 0)})  # platform total
    _policy(
        store,
        QuotaWindow(
            key="month",
            label="Month",
            duration_seconds=2592000,
            anchor="calendar_month",
            limits={"global": QuotaLimits(max_cost_usd=100.0)},
        ),
    )
    status = await _svc(store).evaluate("t1", user_id="u1")
    g = next(s for s in status.windows[0].levels if s.level == "global")
    assert g.usage_cost_usd == 90.0 and g.state == "warn"  # 90% ≥ 80% warn
    assert status.warned and not status.blocked


async def test_level_skipped_when_subject_absent() -> None:
    # A project limit but no project_id in the call → that level is not evaluated.
    store = _FakeStore()
    _policy(
        store,
        QuotaWindow(
            key="s",
            label="S",
            duration_seconds=3600,
            limits={"project": QuotaLimits(max_cost_usd=1.0)},
        ),
    )
    status = await _svc(store).evaluate("t1", user_id="u1")  # no project_id
    present = {s.level for s in status.windows[0].levels}
    assert "project" not in present  # subject absent → not attributed
    assert {"global", "tenant", "user"} <= present  # known subjects ARE shown


# ---- Reset anchoring --------------------------------------------------------


async def test_calendar_month_reset_is_deterministic() -> None:
    store = _FakeStore()
    _policy(
        store,
        QuotaWindow(
            key="month",
            label="Month",
            duration_seconds=2592000,
            anchor="calendar_month",
            limits={"global": QuotaLimits(max_cost_usd=100.0)},
        ),
    )
    status = await _svc(store).evaluate("t1")
    # From 2026-06-08 12:00 → next reset 2026-07-01 00:00 = 22d12h = 1944000s.
    assert status.windows[0].seconds_until_reset == 1944000


def _session_policy(store: _FakeStore) -> None:
    _policy(
        store,
        QuotaWindow(
            key="session",
            label="Session",
            duration_seconds=3600,
            anchor="first_use",
            limits={"user": QuotaLimits(max_cost_usd=100.0)},
        ),
    )


async def test_first_use_session_is_a_fixed_block_from_the_anchor() -> None:
    # Anchor at 11:25 → the session resets at 12:25 (anchor + 1h), a FIXED block,
    # regardless of when the latest call was (Anthropic-style).
    store = _FakeStore(usage={(None, None, "u1"): (1.0, 0)})
    store.anchors["session:user:u1"] = datetime(2026, 6, 8, 11, 25, tzinfo=UTC).isoformat()
    _session_policy(store)
    status = await _svc(store).evaluate("t1", user_id="u1")
    assert status.windows[0].seconds_until_reset == 1500  # 12:25 minus 12:00


async def test_read_only_eval_reports_no_active_session() -> None:
    # No stored anchor + read-only (advance=False) → no active session.
    store = _FakeStore()
    _session_policy(store)
    status = await _svc(store).evaluate("t1", user_id="u1")
    assert status.windows[0].seconds_until_reset is None
    assert store.anchors == {}  # read-only never writes


async def test_guard_opens_a_fixed_session_on_first_call() -> None:
    # The guard (advance=True) opens a new session anchored at now → resets in 1h.
    store = _FakeStore()
    _session_policy(store)
    status = await _svc(store).evaluate("t1", user_id="u1", advance=True)
    assert store.anchors["session:user:u1"] == _NOW.isoformat()
    assert status.windows[0].seconds_until_reset == 3600


async def test_expired_anchor_reopens_a_session_under_advance() -> None:
    # Anchor 2h ago (> 1h duration) → expired → advance reopens at now.
    store = _FakeStore()
    store.anchors["session:user:u1"] = datetime(2026, 6, 8, 10, 0, tzinfo=UTC).isoformat()
    _session_policy(store)
    status = await _svc(store).evaluate("t1", user_id="u1", advance=True)
    assert store.anchors["session:user:u1"] == _NOW.isoformat()
    assert status.windows[0].seconds_until_reset == 3600


# ---- Guard ------------------------------------------------------------------


async def test_guard_raises_on_exceeded() -> None:
    store = _FakeStore(usage={(None, None, "u1"): (12.0, 0)})
    _policy(
        store,
        QuotaWindow(
            key="s",
            label="S",
            duration_seconds=3600,
            limits={"user": QuotaLimits(max_cost_usd=10.0)},
        ),
    )
    guard = QuotaGuard(_svc(store))
    with pytest.raises(QuotaExceededError):
        await guard("t1", user_id="u1")


async def test_guard_warns_without_raising(caplog: pytest.LogCaptureFixture) -> None:
    store = _FakeStore(usage={(None, None, "u1"): (9.0, 0)})
    _policy(
        store,
        QuotaWindow(
            key="s",
            label="S",
            duration_seconds=3600,
            limits={"user": QuotaLimits(max_cost_usd=10.0)},
        ),
    )
    guard = QuotaGuard(_svc(store))
    with caplog.at_level(logging.WARNING):
        await guard("t1", user_id="u1")  # 90% → warn, MUST NOT raise
    assert any("approaching" in r.message for r in caplog.records)


async def test_guard_is_best_effort_on_eval_error() -> None:
    class _Boom:
        async def get_policy(self) -> dict[str, Any] | None:
            raise RuntimeError("arango down")

        async def set_policy(self, doc: dict[str, Any]) -> None: ...

        async def get_anchor(self, key: str) -> str | None:
            return None

        async def set_anchor(self, key: str, iso: str) -> None: ...

        async def usage_in_window(
            self, since_iso: str, **kw: Any
        ) -> tuple[float, int, str | None]:
            return (0.0, 0, None)

    guard = QuotaGuard(QuotaService(_Boom(), clock=lambda: _NOW))
    await guard("t1", user_id="u1")  # swallowed — never breaks an LLM call
