# =============================================================================
# File: test_quota_properties.py
# Version: 2
# Path: ay_platform_core/tests/unit/c8_llm/test_quota_properties.py
# Description: Property-based invariant tests for the quota subsystem, using
#              `hypothesis` (>=6.155). Hypothesis fuzzes each invariant over many
#              generated inputs and SHRINKS any counter-example to a minimal case.
#              The quota evaluation is async, so each generated example runs the
#              coroutine via `asyncio.run` inside a sync `@given` body. Invariants:
#                P1  clamping: a SET narrower cap may never exceed a SET wider one
#                P2  classify boundary: exceeded ⇔ usage >= limit (per dimension)
#                P3  monotonicity: state never improves as usage grows
#                P4  block ⇔ some level of some window is exceeded
#                P5  per-subject isolation: one user's usage can't block another
#
# @relation validates:R-800-042
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ay_platform_core.c8_llm.quota.models import (
    LEVELS_WIDE_TO_NARROW,
    QuotaLimits,
    QuotaPolicy,
    QuotaWindow,
)
from ay_platform_core.c8_llm.quota.service import QuotaService

# A subject signature is (tenant_id, project_id, user_id); the level a usage row
# belongs to is the NARROWEST id that is set (the real eval passes exactly one).
_Subject = tuple[str | None, str | None, str | None]
_RANK = {"ok": 0, "warn": 1, "exceeded": 2}


class _UsageStore:
    """Returns a per-subject usage; evaluate() filters one subject per level."""

    def __init__(self, usage: dict[_Subject, tuple[float, int]]) -> None:
        self.usage = usage
        self._policy: dict[str, Any] | None = None

    async def get_policy(self) -> dict[str, Any] | None:
        return self._policy

    async def set_policy(self, doc: dict[str, Any]) -> None:
        self._policy = doc

    async def get_anchor(self, key: str) -> str | None:
        return None

    async def set_anchor(self, key: str, iso: str) -> None: ...

    async def usage_in_window(
        self,
        since_iso: str,
        *,
        tenant_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[float, int, str | None]:
        cost, tokens = self.usage.get((tenant_id, project_id, user_id), (0.0, 0))
        return (cost, tokens, None)

    async def consumption_by_tenant(
        self, since_iso: str
    ) -> list[tuple[str, float, int]]:
        return []

    async def consumption_by_project(
        self, since_iso: str, tenant_id: str | None = None
    ) -> list[tuple[str, float, int]]:
        return []

    async def consumption_by_user(
        self, since_iso: str, tenant_id: str | None = None
    ) -> list[tuple[str, float, int]]:
        return []

    async def breakdown_by_model(
        self, field: str, value: str, tenant_id: str | None = None
    ) -> list[tuple[str, int, int, float]]:
        return []


def _window(limits: dict[str, QuotaLimits]) -> QuotaWindow:
    # calendar_week → deterministic (no anchor) so usage is summed as-is.
    return QuotaWindow(
        key="week",
        label="W",
        duration_seconds=7 * 86400,
        anchor="calendar_week",
        limits=limits,  # type: ignore[arg-type]
    )


def _policy_doc(limits: dict[str, QuotaLimits]) -> dict[str, Any]:
    return QuotaPolicy(windows=[_window(limits)]).model_dump(mode="json")


_cap = st.one_of(st.none(), st.integers(min_value=1, max_value=1000))


# --- P1: clamping is exactly "a SET child cap may not exceed a SET parent" ---


@pytest.mark.parametrize("dim", ["max_cost_usd", "max_tokens"])
@pytest.mark.parametrize(
    ("wide_level", "narrow_level"),
    [("global", "tenant"), ("tenant", "project"), ("project", "user")],
)
@given(wide=_cap, narrow=_cap)
def test_clamping_matches_the_spec_predicate(
    dim: str, wide_level: str, narrow_level: str, wide: int | None, narrow: int | None
) -> None:
    limits = {
        wide_level: QuotaLimits(**{dim: wide}),
        narrow_level: QuotaLimits(**{dim: narrow}),
    }
    should_fail = wide is not None and narrow is not None and narrow > wide
    if should_fail:
        with pytest.raises(ValueError, match="may not permit more"):
            _window(limits)
    else:
        _window(limits)  # must construct


# --- P2 / P3: classify boundary + monotonicity ------------------------------


async def _state_for(dim: str, cap: int, usage: int) -> str:
    limits = (
        QuotaLimits(max_cost_usd=float(cap)) if dim == "cost" else QuotaLimits(max_tokens=cap)
    )
    use = (float(usage), 0) if dim == "cost" else (0.0, usage)
    store = _UsageStore({("t1", None, None): use})
    await store.set_policy(_policy_doc({"tenant": limits}))
    st_ = await QuotaService(store).evaluate("t1")
    return next(s for s in st_.windows[0].levels if s.level == "tenant").state


@settings(deadline=None, max_examples=300)
@given(
    dim=st.sampled_from(["cost", "tokens"]),
    cap=st.integers(min_value=1, max_value=1000),
    u1=st.integers(min_value=0, max_value=2000),
    u2=st.integers(min_value=0, max_value=2000),
)
def test_exceeded_iff_usage_at_or_above_limit_and_monotone(
    dim: str, cap: int, u1: int, u2: int
) -> None:
    lo, hi = sorted((u1, u2))

    async def _body() -> None:
        s_lo = await _state_for(dim, cap, lo)
        s_hi = await _state_for(dim, cap, hi)
        # P2: exceeded exactly when usage >= cap.
        assert (s_lo == "exceeded") == (lo >= cap)
        assert (s_hi == "exceeded") == (hi >= cap)
        # P3: state never improves as usage grows (lo <= hi).
        assert _RANK[s_lo] <= _RANK[s_hi]

    asyncio.run(_body())


# --- P4: blocked ⇔ some level of some window is exceeded ---------------------


@st.composite
def _clamped_caps(draw: st.DrawFn) -> dict[str, int]:
    g = draw(st.integers(min_value=4, max_value=80))
    t = draw(st.integers(min_value=3, max_value=g))
    p = draw(st.integers(min_value=2, max_value=t))
    u = draw(st.integers(min_value=1, max_value=p))
    return {"global": g, "tenant": t, "project": p, "user": u}


@settings(deadline=None, max_examples=300)
@given(
    caps=_clamped_caps(),
    usage=st.fixed_dictionaries(
        {lvl: st.integers(min_value=0, max_value=90) for lvl in LEVELS_WIDE_TO_NARROW}
    ),
)
def test_blocked_iff_any_level_exceeded(caps: dict[str, int], usage: dict[str, int]) -> None:
    limits = {lvl: QuotaLimits(max_cost_usd=float(c)) for lvl, c in caps.items()}
    store = _UsageStore(
        {
            (None, None, None): (float(usage["global"]), 0),
            ("t1", None, None): (float(usage["tenant"]), 0),
            (None, "p1", None): (float(usage["project"]), 0),
            (None, None, "u1"): (float(usage["user"]), 0),
        }
    )

    async def _body() -> None:
        await store.set_policy(_policy_doc(limits))
        result = await QuotaService(store).evaluate("t1", project_id="p1", user_id="u1")
        expected = any(usage[lvl] >= caps[lvl] for lvl in LEVELS_WIDE_TO_NARROW)
        assert result.blocked is expected
        assert result.blocked is any(s.state == "exceeded" for s in result.windows[0].levels)

    asyncio.run(_body())


# --- P5: per-user isolation — one user's usage can't block another ----------


@settings(deadline=None, max_examples=200)
@given(
    cap=st.integers(min_value=5, max_value=50),
    extra=st.integers(min_value=1, max_value=20),
)
def test_user_usage_does_not_leak_to_another_user(cap: int, extra: int) -> None:
    store = _UsageStore({(None, None, "u1"): (float(cap + extra), 0)})  # only u1 is over

    async def _body() -> None:
        await store.set_policy(_policy_doc({"user": QuotaLimits(max_cost_usd=float(cap))}))
        svc = QuotaService(store)
        assert (await svc.evaluate("t1", user_id="u1")).blocked is True
        assert (await svc.evaluate("t1", user_id="u2")).blocked is False

    asyncio.run(_body())
