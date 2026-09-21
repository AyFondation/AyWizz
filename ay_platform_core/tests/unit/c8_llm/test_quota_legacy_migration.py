# =============================================================================
# File: test_quota_legacy_migration.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_quota_legacy_migration.py
# Description: Unit tests for the pre-four-level quota policy migration in
#              `QuotaService.get_policy()`.
#
#              WHY IT MATTERS MORE THAN A MIGRATION USUALLY DOES. This path
#              runs whenever a STORED policy fails validation, and its failure
#              mode is silent by design: an unrecognisable doc falls back to
#              `QuotaPolicy()` — inert defaults — because a stored policy must
#              never 500 the platform. So a migration that mis-reads a real
#              operator policy does not raise, it QUIETLY LIFTS EVERY SPENDING
#              CAP. The 2026-09-20 branch audit found three of its decisions
#              never taken by a test.
#
#              Driven through the public `get_policy()` rather than the
#              module-private helper: the fallback-to-defaults behaviour is
#              part of the contract being validated, and it lives in the
#              caller.
#
# @relation validates:R-800-144
# =============================================================================

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from ay_platform_core.c8_llm.quota.service import QuotaService

pytestmark = pytest.mark.unit


def _service(stored: Any) -> QuotaService:
    store = AsyncMock()
    store.get_policy.return_value = stored
    return QuotaService(store)


def _legacy_window(**overrides: Any) -> dict[str, Any]:
    """A pre-four-level window: flat caps, no `anchor`, no `limits`.

    `QuotaWindow` is `extra="forbid"`, so the flat `max_*` keys are exactly
    what makes such a doc fail validation and reach the migration.
    """
    window: dict[str, Any] = {
        "key": "month",
        "label": "Monthly",
        "duration_seconds": 2592000,
        "max_cost_usd": 173.0,
        "max_tokens": 5_000_000,
    }
    window.update(overrides)
    return window


async def test_flat_caps_become_the_tenant_level() -> None:
    """The legacy flat caps SHALL land on `tenant`, and the anchor be derived.

    This is the case an existing deployment actually has on disk. If the caps
    were dropped here, `get_policy()` would still return successfully — with
    no limit at any level.
    """
    policy = await _service({"windows": [_legacy_window()]}).get_policy()

    assert len(policy.windows) == 1
    window = policy.windows[0]
    assert window.limits["tenant"].max_cost_usd == 173.0
    assert window.limits["tenant"].max_tokens == 5_000_000
    # Derived from the key, not defaulted to `first_use`: a monthly window
    # anchored on first use would reset per user instead of per calendar
    # month, silently multiplying the effective budget.
    assert window.anchor == "calendar_month"


async def test_a_window_with_only_a_token_cap_keeps_it() -> None:
    """One dimension absent SHALL NOT discard the other.

    The two flat caps are copied by two independent `if ... is not None`
    guards; only the both-present path had been exercised. A policy that caps
    tokens but not cost is a normal configuration, and losing its cap would
    be invisible until the bill arrived.
    """
    window_doc = _legacy_window()
    del window_doc["max_cost_usd"]

    policy = await _service({"windows": [window_doc]}).get_policy()

    tenant = policy.windows[0].limits["tenant"]
    assert tenant.max_tokens == 5_000_000
    assert tenant.max_cost_usd is None


async def test_structured_limits_survive_untouched() -> None:
    """A doc ALREADY carrying `limits` SHALL keep them verbatim.

    Mixed-format docs exist in the wild: a policy re-saved in the new shape
    can still carry a stray legacy key, which trips `extra="forbid"` and
    sends an otherwise-modern doc through the migration. The migration must
    then leave the structured levels alone rather than rebuild them from the
    flat caps — the four-level values are the operator's real intent, and
    collapsing them onto `tenant` would erase the per-user and per-project
    caps entirely.
    """
    stored = {
        "windows": [
            {
                "key": "month",
                "label": "Monthly",
                "duration_seconds": 2592000,
                "anchor": "calendar_month",
                "limits": {
                    "tenant": {"max_cost_usd": 500.0},
                    "user": {"max_cost_usd": 50.0},
                },
                # The stray legacy key that forces the migration path.
                "max_cost_usd": 999.0,
            }
        ]
    }

    policy = await _service(stored).get_policy()

    limits = policy.windows[0].limits
    assert limits["tenant"].max_cost_usd == 500.0
    assert limits["user"].max_cost_usd == 50.0
    # The stray flat value must NOT have overwritten the structured tenant cap.
    assert limits["tenant"].max_cost_usd != 999.0


def _assert_inert_defaults(policy: Any) -> None:
    """The fallback shape — and the reason these tests exist.

    `QuotaPolicy()` is NOT empty: it carries the three standard windows with
    `limits={}`. So the fallback does not disable quota accounting, it keeps
    the windows and removes every CAP. A policy that fails to migrate
    therefore enforces nothing, silently, while `/quota/me` keeps answering
    200 — which is exactly why the migration's own branches are worth
    pinning rather than trusting.
    """
    assert [w.key for w in policy.windows] == ["session", "week", "month"]
    assert all(w.limits == {} for w in policy.windows)


async def test_a_malformed_window_entry_falls_back_to_defaults() -> None:
    """A non-dict inside `windows` SHALL abandon the migration, not crash.

    Reaching `w.get(...)` on a string would raise inside `get_policy()`,
    which sits on the request path of `/quota/me` and the operator console —
    the source's own comment says a stored policy must never 500 the
    platform.
    """
    policy = await _service({"windows": [_legacy_window(), "not-a-window"]}).get_policy()

    _assert_inert_defaults(policy)


async def test_windows_not_a_list_falls_back_to_defaults() -> None:
    policy = await _service({"windows": {"key": "month"}}).get_policy()

    _assert_inert_defaults(policy)


async def test_absent_policy_document_yields_defaults() -> None:
    """No stored doc at all SHALL NOT reach the migration."""
    policy = await _service(None).get_policy()

    _assert_inert_defaults(policy)
