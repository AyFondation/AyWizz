# =============================================================================
# File: test_routing_decision_ledger.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_routing_decision_ledger.py
# Description: Unit tests for freezing WHY a model was chosen into the call
#              ledger, alongside the cost it explains (2026-09-29).
#
#              THE GUARANTEE UNDER TEST is the same one `cost_usd` already
#              had and the reasoning did not: a value written at call time
#              is never re-derived later. Benchmark scores get updated,
#              price lists change, the agent→profile table is edited — and
#              a question asked in three months ("why did this run pick
#              Opus, and why did it cost that?") must be answered with the
#              inputs of the day, not today's.
#
# @relation validates:R-800-146
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ay_platform_core.c8_llm.callbacks.cost_tracker import (
    _extract_routing,
    build_call_record,
)
from ay_platform_core.c8_llm.config import ModelInfo
from ay_platform_core.c8_llm.models import CostCallEnvelope, RoutingDecision

pytestmark = pytest.mark.unit


def _envelope(**headers: str) -> CostCallEnvelope:
    now = datetime.now(UTC)
    return CostCallEnvelope(
        model="anthropic/claude-x",
        start_time=now,
        end_time=now,
        status="success",
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
        headers={"X-Tenant-Id": "t-1", "X-Agent-Name": "architect", **headers},
    )


_DECISION = RoutingDecision(
    quality_requested="medium",
    quality_resolved="high",
    benchmarks={"swe-bench": 0.7, "gpqa": 0.3},
    composite_score=82.5,
    effective_cost=4.25,
    decided_by="benchmark-ratio",
)


# ---------------------------------------------------------------------------
# The decision survives the wire
# ---------------------------------------------------------------------------


def test_the_decision_round_trips_through_the_header() -> None:
    """Every field must survive client → proxy → receiver.

    The decision travels as one header because that is how every other tag
    already reaches the cost receiver. A field lost in transit is not
    recoverable afterwards, which is the entire reason for capturing it at
    call time rather than re-deriving it.
    """
    record = build_call_record(
        _envelope(**{"X-Routing-Decision": _DECISION.model_dump_json()}),
        catalog={},
    )

    assert record.tags.routing == _DECISION


def test_an_upgraded_tier_is_visible_in_the_record() -> None:
    """Requested and resolved are stored SEPARATELY.

    Resolution accepts any model above the requested floor, so a `medium`
    request can legitimately be served — and billed — at `high`. Storing
    only one of the two would leave a reviewer unable to tell a deliberate
    upgrade from a misrouted call.
    """
    record = build_call_record(
        _envelope(**{"X-Routing-Decision": _DECISION.model_dump_json()}),
        catalog={},
    )

    assert record.tags.routing.quality_requested == "medium"
    assert record.tags.routing.quality_resolved == "high"


def test_the_ranking_inputs_are_kept_not_just_the_verdict() -> None:
    """The profile, the score and the effective cost are all stored.

    `decided_by` alone would say a ratio won without saying which ratio.
    The effective cost in particular is NOT recoverable from list prices
    later: it carries the caching discount and the input/output weighting
    in force at the time, both of which are tunable.
    """
    routing = build_call_record(
        _envelope(**{"X-Routing-Decision": _DECISION.model_dump_json()}),
        catalog={},
    ).tags.routing

    assert routing.benchmarks == {"swe-bench": 0.7, "gpqa": 0.3}
    assert routing.composite_score == 82.5
    assert routing.effective_cost == 4.25
    assert routing.decided_by == "benchmark-ratio"


# ---------------------------------------------------------------------------
# Absence and corruption are handled honestly
# ---------------------------------------------------------------------------


def test_a_call_without_a_decision_records_an_empty_one() -> None:
    """No header = nothing was decided, NOT "price decided".

    A call with an explicit model, or through a legacy agent route, never
    went through catalogue resolution. Defaulting `decided_by` to `price`
    would put a reason in the ledger that no code ever reached.
    """
    routing = build_call_record(_envelope(), catalog={}).tags.routing

    assert routing == RoutingDecision()
    assert routing.decided_by is None


def test_a_malformed_header_never_costs_the_call_its_cost() -> None:
    """Parsing runs inside a cost callback, so it must not raise.

    Losing the reason for one call leaves a gap in the audit trail. Raising
    here would lose the call's COST — the ledger is the billing record
    before it is an explanation of itself, so the failure mode has to fall
    on the explanation.
    """
    record = build_call_record(
        _envelope(**{"X-Routing-Decision": "{not json at all"}),
        catalog={
            "anthropic/claude-x": ModelInfo(
                display_name="Claude X",
                features=[],
                context_window=200000,
                cost_per_million_input=3.0,
                cost_per_million_output=15.0,
            ),
        },
    )

    assert record.tags.routing == RoutingDecision()
    # The cost still landed: 1000 in + 500 out at 3 / 15 per 1M.
    assert record.cost_usd == pytest.approx(0.0105)


def test_a_wellformed_but_unexpected_payload_is_rejected_not_absorbed() -> None:
    """`extra="forbid"` on the model, so a renamed field fails loudly here
    rather than being silently dropped into an empty-looking decision that
    reads as "no routing happened"."""
    assert _extract_routing('{"decided_by": "price", "surprise": 1}') == (
        RoutingDecision()
    )


def test_only_the_populated_fields_travel() -> None:
    """The header is serialised with `exclude_defaults`, keeping it small on
    a hot path — and the receiver must still rebuild an equal object."""
    sparse = RoutingDecision(decided_by="price", quality_requested="low")
    wire = sparse.model_dump_json(exclude_defaults=True)

    assert "composite_score" not in wire
    assert _extract_routing(wire) == sparse
