# =============================================================================
# File: service.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/service.py
# Description: QuotaService — the global LLM quota policy + FOUR-LEVEL per-subject
#              evaluation. For a call attributed to (tenant, project, user) it
#              sums usage at each level present in a window (global aggregate /
#              tenant / project / user), classifies ok/warn/exceeded, and blocks
#              if ANY level is exceeded. Windows anchor first-use (session,
#              trailing window in v2) or the calendar (week/month). Pure of HTTP.
# =============================================================================

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ay_platform_core.c8_llm.quota.models import (
    LEVELS_WIDE_TO_NARROW,
    QuotaLevel,
    QuotaLevelStatus,
    QuotaLimits,
    QuotaPolicy,
    QuotaState,
    QuotaStatus,
    QuotaWindow,
    QuotaWindowStatus,
)
from ay_platform_core.c8_llm.quota.repository import QuotaStore


def _pct(usage: float, limit: float | None) -> float | None:
    if limit is None or limit <= 0:
        return None
    return round(usage / limit * 100.0, 2)


def _classify(limits: QuotaLimits, cost: float, tokens: int) -> QuotaState:
    if (limits.max_cost_usd is not None and cost >= limits.max_cost_usd) or (
        limits.max_tokens is not None and tokens >= limits.max_tokens
    ):
        return "exceeded"
    return "ok"  # warn computed by the caller against warn_threshold_pct


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return now.replace(
        year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _week_start(now: datetime) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=now.weekday())  # Monday=0


class QuotaService:
    """Owns the global policy and evaluates a subject's per-level usage."""

    def __init__(
        self,
        store: QuotaStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    async def get_policy(self) -> QuotaPolicy:
        doc = await self._store.get_policy()
        if doc is None:
            return QuotaPolicy()
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        return QuotaPolicy.model_validate(clean)

    async def set_policy(self, windows: list[QuotaWindow]) -> QuotaPolicy:
        policy = QuotaPolicy(windows=windows, updated_at=self._clock())
        await self._store.set_policy(policy.model_dump(mode="json"))
        return policy

    def _calendar_bounds(self, w: QuotaWindow, now: datetime) -> tuple[str, int] | None:
        """Calendar windows: (`since_iso`, seconds_until_reset), deterministic.
        None for first_use (which anchors PER SUBJECT — see `_session_bounds`)."""
        if w.anchor == "calendar_week":
            start = _week_start(now)
            reset = start + timedelta(days=7)
        elif w.anchor == "calendar_month":
            start = _month_start(now)
            reset = _next_month_start(now)
        else:
            return None
        return start.isoformat(), max(0, int(reset.timestamp() - now.timestamp()))

    async def _session_bounds(
        self, w: QuotaWindow, level: QuotaLevel, subject_id: str, now: datetime, advance: bool
    ) -> tuple[str, int | None]:
        """First-use session = a FIXED `duration` block from the subject's first
        call (Anthropic-style: started 5h35 → resets 10h35, even under continuous
        activity). The guard (`advance=True`) opens a new session when none is
        active; read-only callers report no active session (usage since `now`)."""
        key = f"{w.key}:{level}:{subject_id}"
        anchor = _parse_iso(await self._store.get_anchor(key))
        if anchor is None or now.timestamp() >= anchor.timestamp() + w.duration_seconds:
            if not advance:
                return now.isoformat(), None  # no active session
            anchor = now
            await self._store.set_anchor(key, now.isoformat())
        reset = max(0, int(anchor.timestamp() + w.duration_seconds - now.timestamp()))
        return anchor.isoformat(), reset

    async def evaluate(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        user_id: str | None = None,
        advance: bool = False,
    ) -> QuotaStatus:
        """Evaluate the (tenant, project, user) subject against every window and
        every level whose subject is known. `advance=True` (the guard) opens a
        new first-use session when none is active; read-only callers don't."""
        policy = await self.get_policy()
        now = self._clock()
        subjects: dict[QuotaLevel, dict[str, str]] = {"global": {}}
        if tenant_id:
            subjects["tenant"] = {"tenant_id": tenant_id}
        if project_id:
            subjects["project"] = {"project_id": project_id}
        if user_id:
            subjects["user"] = {"user_id": user_id}

        window_statuses: list[QuotaWindowStatus] = []
        warned = blocked = False
        for w in policy.windows:
            cal = self._calendar_bounds(w, now)
            level_statuses: list[QuotaLevelStatus] = []
            window_state: QuotaState = "ok"
            window_reset: int | None = None
            binding_pct = -1.0
            for level in LEVELS_WIDE_TO_NARROW:
                if level not in subjects:
                    continue  # subject id absent → cannot attribute this level
                subject = subjects[level]
                subject_id = next(iter(subject.values()), "_")
                since_iso: str
                reset_secs: int | None
                if cal is not None:
                    since_iso, reset_secs = cal
                else:
                    since_iso, reset_secs = await self._session_bounds(
                        w, level, subject_id, now, advance
                    )
                # Usage for every known-subject level, limit or not, so the
                # display always shows consumption (state stays `ok` with no cap).
                limits = w.limits.get(level) or QuotaLimits()
                cost, tokens, _ = await self._store.usage_in_window(since_iso, **subject)
                ls = _level_status(level, limits, cost, tokens, w.warn_threshold_pct)
                if ls.state == "exceeded":
                    blocked = True
                    window_state = "exceeded"
                elif ls.state == "warn":
                    warned = True
                    window_state = "warn" if window_state == "ok" else window_state
                # The binding (most-constraining) level drives the flat mirror +
                # the displayed reset countdown.
                lp = max([p for p in (ls.cost_pct, ls.tokens_pct) if p is not None], default=0.0)
                if lp > binding_pct:
                    binding_pct, window_reset = lp, reset_secs
                level_statuses.append(ls)
            binding = _binding_level(level_statuses)
            window_statuses.append(
                QuotaWindowStatus(
                    key=w.key,
                    label=w.label,
                    duration_seconds=w.duration_seconds,
                    anchor=w.anchor,
                    seconds_until_reset=window_reset,
                    state=window_state,
                    levels=level_statuses,
                    usage_cost_usd=binding.usage_cost_usd if binding else 0.0,
                    usage_tokens=binding.usage_tokens if binding else 0,
                    max_cost_usd=binding.max_cost_usd if binding else None,
                    max_tokens=binding.max_tokens if binding else None,
                    cost_pct=binding.cost_pct if binding else None,
                    tokens_pct=binding.tokens_pct if binding else None,
                )
            )
        return QuotaStatus(
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=user_id,
            windows=window_statuses,
            warned=warned,
            blocked=blocked,
        )


def _level_status(
    level: QuotaLevel, limits: QuotaLimits, cost: float, tokens: int, warn_pct: float
) -> QuotaLevelStatus:
    cost_pct = _pct(cost, limits.max_cost_usd)
    max_tok = float(limits.max_tokens) if limits.max_tokens else None
    tokens_pct = _pct(float(tokens), max_tok)
    state = _classify(limits, cost, tokens)
    if state == "ok":
        pcts = [p for p in (cost_pct, tokens_pct) if p is not None]
        if any(p >= warn_pct for p in pcts):
            state = "warn"
    return QuotaLevelStatus(
        level=level,
        usage_cost_usd=round(cost, 6),
        usage_tokens=tokens,
        max_cost_usd=limits.max_cost_usd,
        max_tokens=limits.max_tokens,
        cost_pct=cost_pct,
        tokens_pct=tokens_pct,
        state=state,
    )


def _binding_level(levels: list[QuotaLevelStatus]) -> QuotaLevelStatus | None:
    """The most-constraining level: the highest usage pct (None pct = 0)."""
    if not levels:
        return None
    return max(
        levels,
        key=lambda s: max(
            (p for p in (s.cost_pct, s.tokens_pct) if p is not None), default=0.0
        ),
    )


def _parse_iso(iso: str | None) -> datetime | None:
    """Parse a stored anchor timestamp; naive values are assumed UTC."""
    if iso is None:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
