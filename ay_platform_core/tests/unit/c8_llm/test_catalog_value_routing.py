# =============================================================================
# File: test_catalog_value_routing.py
# Version: 1
# Path: ay_platform_core/tests/unit/c8_llm/test_catalog_value_routing.py
# Description: Unit tests for quality-per-euro model resolution (2026-09-29).
#
#              WHY A SEPARATE FILE. `test_tenant_catalog.py` covers catalogue
#              mechanics — scoping, enablement, isolation. These cover the
#              CHOICE: which of several admissible models the platform spends
#              money on, and why. The distinction is worth a file boundary
#              because the selection rule is the part most likely to be
#              tuned, and a tuning should break tests that are about tuning.
#
#              THESE TESTS EXIST BECAUSE THE OLD ONES COULD NOT FAIL. Both the
#              quality floor and the cost function changed on 2026-09-29 and
#              the whole suite stayed green: the seed helper pinned
#              `cost_out = cost_in * 5`, so input-only ranking and blended
#              ranking agree by construction, and no test ever offered a
#              higher tier at a lower price. Every test below is built to
#              DISAGREE with the previous behaviour.
#
# @relation validates:R-800-152
# =============================================================================

from __future__ import annotations

import pytest

from ay_platform_core.c8_llm.registry.catalog_models import TenantCatalogUpsert
from ay_platform_core.c8_llm.registry.models import ModelQuality
from tests.unit.c8_llm.test_tenant_catalog import (
    _TENANT,
    _registry,
    _seed_registry,
    _service,
)

pytestmark = pytest.mark.unit


async def _resolve(reg, aliases: list[str], **kwargs):  # type: ignore[no-untyped-def]
    svc, _ = _service(reg)
    for model_id in aliases:
        await svc.upsert_model(_TENANT, model_id, TenantCatalogUpsert())
    return await svc.resolve(_TENANT, **kwargs)


# ---------------------------------------------------------------------------
# The quality FLOOR — "at least this good", not "exactly this tier"
# ---------------------------------------------------------------------------


async def test_a_better_model_that_costs_less_is_chosen() -> None:
    """Asking for `medium` SHALL consider a cheaper `high`.

    The exact-tier match declined a model that is better on BOTH axes. There
    is no reading of "optimise cost and quality" under which paying more for
    less is right; it was simply a filter written as equality where the
    domain is ordinal.
    """
    reg = _registry()
    mid = await _seed_registry(
        reg, "mid-pricey", quality=ModelQuality.MEDIUM, cost_in=10.0, cost_out=10.0,
    )
    top = await _seed_registry(
        reg, "high-cheap", quality=ModelQuality.HIGH, cost_in=1.0, cost_out=1.0,
    )

    resolved = await _resolve(reg, [mid, top], model_quality=ModelQuality.MEDIUM)

    assert resolved is not None
    assert resolved.model_alias == "high-cheap"


async def test_the_reported_tier_is_the_model_s_own_not_the_request() -> None:
    """An upgrade must be visible in the resolution, or a cost report cannot
    explain why a `medium` request was billed at `high` rates."""
    reg = _registry()
    top = await _seed_registry(
        reg, "high-cheap", quality=ModelQuality.HIGH, cost_in=1.0, cost_out=1.0,
    )

    resolved = await _resolve(reg, [top], model_quality=ModelQuality.LOW)

    assert resolved is not None
    assert resolved.model_quality is ModelQuality.HIGH


async def test_the_floor_still_refuses_to_go_below_it() -> None:
    """Widening upward must not become a silent downgrade: a `low` model can
    never answer a `high` request, however cheap."""
    reg = _registry()
    cheap = await _seed_registry(
        reg, "low-cheap", quality=ModelQuality.LOW, cost_in=0.1, cost_out=0.1,
    )

    assert await _resolve(reg, [cheap], model_quality=ModelQuality.HIGH) is None


# ---------------------------------------------------------------------------
# The PRICE half — output cost, and the caching discount
# ---------------------------------------------------------------------------


async def test_output_price_can_decide_the_winner() -> None:
    """Ranking on input price alone described almost none of the bill.

    `talker` is cheaper to prompt and far more expensive to answer. A
    `generate` phase is dominated by output, so the input-only ranking
    picked the model that costs more to actually use.
    """
    reg = _registry()
    talker = await _seed_registry(
        reg, "cheap-in-dear-out", quality=ModelQuality.LOW,
        cost_in=1.0, cost_out=200.0,
    )
    balanced = await _seed_registry(
        reg, "dearer-in-cheap-out", quality=ModelQuality.LOW,
        cost_in=2.0, cost_out=2.0,
    )

    resolved = await _resolve(reg, [talker, balanced], model_quality=ModelQuality.LOW)

    assert resolved is not None
    assert resolved.model_alias == "dearer-in-cheap-out"


async def test_measured_caching_discounts_the_input_half() -> None:
    """A model that caches re-sends this platform's long system prompt for a
    fraction of the price, and the ranking must see that.

    The two models are priced so the cacher loses on the raw numbers and
    wins once its MEASURED capability is credited — which is the only way
    this test can distinguish the discount from its absence.
    """
    reg = _registry()
    cacher = await _seed_registry(
        reg, "caches", quality=ModelQuality.LOW,
        cost_in=10.0, cost_out=1.0, prompt_caching=True,
    )
    plain = await _seed_registry(
        reg, "no-cache", quality=ModelQuality.LOW,
        cost_in=6.0, cost_out=1.0, prompt_caching=False,
    )

    resolved = await _resolve(reg, [cacher, plain], model_quality=ModelQuality.LOW)

    assert resolved is not None
    assert resolved.model_alias == "caches"


# ---------------------------------------------------------------------------
# The QUALITY half — benchmark score, and the refusal to invent one
# ---------------------------------------------------------------------------


async def test_a_better_score_justifies_a_higher_price() -> None:
    """The point of a ratio: not the cheapest model, the best value.

    `strong` costs twice as much and scores three times better, so it is the
    better buy. Price-only ranking picks `weak` — which is precisely the
    behaviour "best quality/price, not price at the expense of quality" was
    meant to end.
    """
    reg = _registry()
    weak = await _seed_registry(
        reg, "weak-cheap", quality=ModelQuality.LOW,
        cost_in=1.0, cost_out=1.0, quality_scores={"swe-bench": 30.0},
    )
    strong = await _seed_registry(
        reg, "strong-dearer", quality=ModelQuality.LOW,
        cost_in=2.0, cost_out=2.0, quality_scores={"swe-bench": 90.0},
    )

    resolved = await _resolve(
        reg, [weak, strong], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 1.0},
    )

    assert resolved is not None
    assert resolved.model_alias == "strong-dearer"
    assert resolved.decided_by_benchmark == "swe-bench"


async def test_the_winner_changes_with_the_benchmark_asked_for() -> None:
    """THE REASON SCORES ARE A DICTIONARY, in one test.

    A coding specialist and a summarisation specialist, priced identically.
    Neither is "better"; which one is the right spend depends entirely on
    the work. A single averaged score would pick the same model for both
    jobs and be wrong for one of them every time.
    """
    reg = _registry()
    coder = await _seed_registry(
        reg, "coder", quality=ModelQuality.LOW, cost_in=1.0, cost_out=1.0,
        quality_scores={"swe-bench": 90.0, "doc-synthesis": 40.0},
    )
    writer = await _seed_registry(
        reg, "writer", quality=ModelQuality.LOW, cost_in=1.0, cost_out=1.0,
        quality_scores={"swe-bench": 35.0, "doc-synthesis": 95.0},
    )

    for_code = await _resolve(
        reg, [coder, writer], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 1.0},
    )
    for_docs = await _resolve(
        reg, [coder, writer], model_quality=ModelQuality.LOW,
        benchmarks={"doc-synthesis": 1.0},
    )

    assert for_code is not None and for_code.model_alias == "coder"
    assert for_docs is not None and for_docs.model_alias == "writer"


async def test_the_weights_decide_when_a_profile_spans_two_benchmarks() -> None:
    """A profile is WEIGHTED, and the weights must actually move the answer.

    Same two specialists, same price. Weighting the profile toward coding
    picks the coder; toward synthesis picks the writer. If the weights were
    ignored — averaged flat, or only the first key read — one of these two
    assertions would fail, which is the only way to prove they are applied.
    """
    reg = _registry()
    coder = await _seed_registry(
        reg, "coder", quality=ModelQuality.LOW, cost_in=1.0, cost_out=1.0,
        quality_scores={"swe-bench": 90.0, "doc-synthesis": 40.0},
    )
    writer = await _seed_registry(
        reg, "writer", quality=ModelQuality.LOW, cost_in=1.0, cost_out=1.0,
        quality_scores={"swe-bench": 40.0, "doc-synthesis": 90.0},
    )

    code_heavy = await _resolve(
        reg, [coder, writer], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 0.9, "doc-synthesis": 0.1},
    )
    doc_heavy = await _resolve(
        reg, [coder, writer], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 0.1, "doc-synthesis": 0.9},
    )

    assert code_heavy is not None and code_heavy.model_alias == "coder"
    assert doc_heavy is not None and doc_heavy.model_alias == "writer"
    # The label names the whole profile, sorted, so it is stable run to run.
    assert code_heavy.decided_by_benchmark == "doc-synthesis,swe-bench"


async def test_partial_coverage_of_a_profile_excludes_a_model() -> None:
    """Covering HALF a profile is a different measurement, not a smaller one.

    `specialist` is cheaper and scores 100 on the one benchmark it has. If
    its composite were taken over its own subset it would win every time —
    a model could then top the ranking by being scored on as little as
    possible. Only models covering the whole profile compete.
    """
    reg = _registry()
    specialist = await _seed_registry(
        reg, "half-covered", quality=ModelQuality.LOW, cost_in=1.0, cost_out=1.0,
        quality_scores={"swe-bench": 100.0},
    )
    rounded = await _seed_registry(
        reg, "fully-covered", quality=ModelQuality.LOW, cost_in=4.0, cost_out=4.0,
        quality_scores={"swe-bench": 60.0, "gpqa": 60.0},
    )

    resolved = await _resolve(
        reg, [specialist, rounded], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 0.5, "gpqa": 0.5},
    )

    assert resolved is not None
    assert resolved.model_alias == "fully-covered"


async def test_price_still_decides_between_equally_scored_models() -> None:
    """Equal quality on the benchmark asked for, so the ratio reduces to
    price — and should."""
    reg = _registry()
    dear = await _seed_registry(
        reg, "same-dear", quality=ModelQuality.LOW,
        cost_in=9.0, cost_out=9.0, quality_scores={"swe-bench": 70.0},
    )
    cheap = await _seed_registry(
        reg, "same-cheap", quality=ModelQuality.LOW,
        cost_in=3.0, cost_out=3.0, quality_scores={"swe-bench": 70.0},
    )

    resolved = await _resolve(
        reg, [dear, cheap], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 1.0},
    )

    assert resolved is not None
    assert resolved.model_alias == "same-cheap"


async def test_unscored_on_the_named_benchmark_is_not_given_a_number() -> None:
    """Scored and unscored are never ranked against each other, per benchmark.

    Defaulting a missing score to 0 would bury the model; to the mean would
    flatter it; borrowing its score from ANOTHER benchmark would be worse
    still, since that is exactly the conflation the dictionary exists to
    prevent. Here the unscored model is cheaper, holds a high score on a
    DIFFERENT benchmark, and still loses.
    """
    reg = _registry()
    elsewhere = await _seed_registry(
        reg, "great-at-something-else", quality=ModelQuality.LOW,
        cost_in=1.0, cost_out=1.0, quality_scores={"doc-synthesis": 99.0},
    )
    scored = await _seed_registry(
        reg, "scored-dearer", quality=ModelQuality.LOW,
        cost_in=5.0, cost_out=5.0, quality_scores={"swe-bench": 80.0},
    )

    resolved = await _resolve(
        reg, [elsewhere, scored], model_quality=ModelQuality.LOW,
        benchmarks={"swe-bench": 1.0},
    )

    assert resolved is not None
    assert resolved.model_alias == "scored-dearer"


async def test_without_a_benchmark_the_choice_is_price_and_says_so() -> None:
    """No benchmark named, so nothing is known about relative quality and
    price decides. `decided_by_benchmark` must report that honestly — a
    resolution that claimed a quality judgement it never made would be
    unauditable."""
    reg = _registry()
    dear = await _seed_registry(
        reg, "plain-dear", quality=ModelQuality.LOW, cost_in=8.0, cost_out=8.0,
        quality_scores={"swe-bench": 99.0},
    )
    cheap = await _seed_registry(
        reg, "plain-cheap", quality=ModelQuality.LOW, cost_in=2.0, cost_out=2.0,
    )

    resolved = await _resolve(reg, [dear, cheap], model_quality=ModelQuality.LOW)

    assert resolved is not None
    assert resolved.model_alias == "plain-cheap"
    assert resolved.decided_by_benchmark is None


async def test_an_unknown_benchmark_falls_back_to_price_not_to_nothing() -> None:
    """Naming a benchmark no candidate carries must not empty the candidate
    set — the models are still admissible, only the quality signal is
    missing."""
    reg = _registry()
    dear = await _seed_registry(
        reg, "unknown-dear", quality=ModelQuality.LOW, cost_in=8.0, cost_out=8.0,
    )
    cheap = await _seed_registry(
        reg, "unknown-cheap", quality=ModelQuality.LOW, cost_in=2.0, cost_out=2.0,
    )

    resolved = await _resolve(
        reg, [dear, cheap], model_quality=ModelQuality.LOW,
        benchmarks={"a-benchmark-nobody-has": 1.0},
    )

    assert resolved is not None
    assert resolved.model_alias == "unknown-cheap"
    assert resolved.decided_by_benchmark is None


# ---------------------------------------------------------------------------
# The new capability gates
# ---------------------------------------------------------------------------


async def test_structured_output_is_a_hard_gate_not_a_preference() -> None:
    """A phase that needs a schema cannot be served by a model that free-
    texts, however cheap — so the gate excludes rather than deprioritises."""
    reg = _registry()
    plain = await _seed_registry(
        reg, "cheap-no-schema", quality=ModelQuality.LOW,
        cost_in=0.1, cost_out=0.1, structured_output=False,
    )
    schema = await _seed_registry(
        reg, "dear-schema", quality=ModelQuality.LOW,
        cost_in=50.0, cost_out=50.0, structured_output=True,
    )

    resolved = await _resolve(
        reg, [plain, schema],
        model_quality=ModelQuality.LOW, require_structured_output=True,
    )

    assert resolved is not None
    assert resolved.model_alias == "dear-schema"


async def test_streaming_gate_excludes_a_non_streaming_model() -> None:
    """C3 renders chat token by token; a non-streaming model does not degrade
    it quietly, it leaves the user looking at nothing."""
    reg = _registry()
    batch = await _seed_registry(
        reg, "no-stream", quality=ModelQuality.LOW,
        cost_in=0.1, cost_out=0.1, streaming=False,
    )

    assert await _resolve(
        reg, [batch], model_quality=ModelQuality.LOW, require_streaming=True,
    ) is None


async def test_resolution_reports_what_the_caller_may_use() -> None:
    """The resolved model carries the EFFECTIVE capabilities, so a caller
    never has to re-derive them — and never acts on one the operator
    disabled."""
    reg = _registry()
    full = await _seed_registry(
        reg, "capable", quality=ModelQuality.LOW, cost_in=1.0, cost_out=1.0,
        structured_output=True, streaming=True, prompt_caching=True,
    )

    resolved = await _resolve(reg, [full], model_quality=ModelQuality.LOW)

    assert resolved is not None
    assert resolved.structured_output is True
    assert resolved.streaming is True
    assert resolved.prompt_caching is True
