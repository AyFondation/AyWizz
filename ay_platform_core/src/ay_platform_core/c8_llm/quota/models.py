# =============================================================================
# File: models.py
# Version: 2
# Path: ay_platform_core/src/ay_platform_core/c8_llm/quota/models.py
# Description: Pydantic contracts for the LLM quota subsystem. v2 generalises the
#              Lot 3 single-per-tenant policy into a FOUR-LEVEL model: every
#              rolling/anchored window carries caps at four scopes —
#              global (platform aggregate), tenant, project, user — and a call is
#              blocked if ANY applicable level is exceeded (the most constraining
#              wins). Caps are clamped `user <= project <= tenant <= global` per
#              dimension. Windows anchor either to first-use (session) or the
#              calendar (week/month).
# =============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QuotaState = Literal["ok", "warn", "exceeded"]

# The four nested scopes a call is attributed to, ordered widest → narrowest.
# Enforcement blocks on the first exceeded level; clamping requires the value
# at a narrower level to never exceed the wider one.
QuotaLevel = Literal["global", "tenant", "project", "user"]
LEVELS_WIDE_TO_NARROW: tuple[QuotaLevel, ...] = ("global", "tenant", "project", "user")

# How a window's start (and thus its reset) is anchored.
#  - first_use:      session starts at the first call, resets `duration` later
#                    (Anthropic-style); the next call opens a new session.
#  - calendar_week:  resets Monday 00:00 UTC.
#  - calendar_month: resets the 1st 00:00 UTC.
AnchorMode = Literal["first_use", "calendar_week", "calendar_month"]

_HOUR = 3600
_DAY = 86400


class QuotaLimits(BaseModel):
    """One scope's caps for a window. Both None = observational (no block)."""

    model_config = ConfigDict(extra="forbid")

    max_cost_usd: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=0)


class QuotaWindow(BaseModel):
    """One rolling/anchored window of the policy, with caps at each of the four
    levels. Caps are clamped `user <= project <= tenant <= global` per
    dimension; absent levels (all-None) are no-ops."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=64)
    duration_seconds: int = Field(gt=0)
    anchor: AnchorMode = "first_use"
    warn_threshold_pct: float = Field(default=80.0, ge=0, le=100)
    limits: dict[QuotaLevel, QuotaLimits] = Field(default_factory=dict)

    def level_limits(self, level: QuotaLevel) -> QuotaLimits:
        return self.limits.get(level, QuotaLimits())

    @model_validator(mode="after")
    def _clamp_hierarchy(self) -> QuotaWindow:
        """A narrower level may not permit MORE than a wider one (a tenant can't
        be allowed more than the platform, a project more than its tenant, a
        user more than its project). Enforced per dimension; a wider level's
        None (unlimited) imposes no constraint."""
        prev: QuotaLimits | None = None
        prev_level: QuotaLevel | None = None
        for level in LEVELS_WIDE_TO_NARROW:
            cur = self.limits.get(level)
            if cur is None:
                continue
            if prev is not None:
                for dim in ("max_cost_usd", "max_tokens"):
                    wide = getattr(prev, dim)
                    narrow = getattr(cur, dim)
                    if wide is not None and narrow is not None and narrow > wide:
                        raise ValueError(
                            f"window {self.key!r}: {level}.{dim}={narrow} exceeds "
                            f"{prev_level}.{dim}={wide} (a narrower level may not "
                            "permit more than a wider one)"
                        )
            prev, prev_level = cur, level
        return self


def _default_windows() -> list[QuotaWindow]:
    """Durations + anchors set, limits OPEN (empty) so the feature stays inert
    until an operator configures real caps (no surprise blocking)."""
    return [
        QuotaWindow(
            key="session",
            label="Session (5h)",
            duration_seconds=5 * _HOUR,
            anchor="first_use",
        ),
        QuotaWindow(
            key="week", label="Week", duration_seconds=7 * _DAY, anchor="calendar_week"
        ),
        QuotaWindow(
            key="month",
            label="Month",
            duration_seconds=30 * _DAY,
            anchor="calendar_month",
        ),
    ]


class QuotaPolicy(BaseModel):
    """The single global policy. Same windows + per-level caps for everyone."""

    model_config = ConfigDict(extra="forbid")

    windows: list[QuotaWindow] = Field(default_factory=_default_windows)
    updated_at: datetime | None = None


class QuotaPolicyUpdate(BaseModel):
    """PUT body — replaces the window set wholesale (tenant_manager)."""

    model_config = ConfigDict(extra="forbid")

    windows: list[QuotaWindow]


class QuotaLevelStatus(BaseModel):
    """One scope's evaluation within a window for a specific subject."""

    model_config = ConfigDict(extra="forbid")

    level: QuotaLevel
    usage_cost_usd: float
    usage_tokens: int
    max_cost_usd: float | None
    max_tokens: int | None
    cost_pct: float | None
    tokens_pct: float | None
    state: QuotaState


class QuotaWindowStatus(BaseModel):
    """Per-window evaluation: the worst state across levels + each level's
    breakdown. The flat `usage_*`/`max_*`/`*_pct` fields mirror the MOST
    CONSTRAINING level (highest pct, or the exceeded one) so a simple consumer
    can render a single bar without walking `levels`. `seconds_until_reset` is
    when the window next refreshes."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    duration_seconds: int
    anchor: AnchorMode
    seconds_until_reset: int | None = None
    state: QuotaState
    levels: list[QuotaLevelStatus]
    # Flat mirror of the most-constraining level (backward-compatible view).
    usage_cost_usd: float = 0.0
    usage_tokens: int = 0
    max_cost_usd: float | None = None
    max_tokens: int | None = None
    cost_pct: float | None = None
    tokens_pct: float | None = None


class QuotaStatus(BaseModel):
    """A subject's evaluation across every window. `blocked` iff any level of any
    window is `exceeded` (drives the hard 429 at the gateway)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str | None = None
    user_id: str | None = None
    windows: list[QuotaWindowStatus]
    warned: bool
    blocked: bool
